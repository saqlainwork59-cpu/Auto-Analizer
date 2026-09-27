"""Alert delivery: Telegram, email and in-browser notifications. Each (rule, signal) pair is
delivered at most once (unique constraint), and every attempt is recorded."""
from __future__ import annotations

import html
import logging
from email.message import EmailMessage

import aiosmtplib
import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.setups import fmt
from app.config import get_settings
from app.core import flags
from app.core.bus import CH_SIGNALS, bus
from app.models import AlertDelivery, AlertRule, Signal, WatchlistItem
from app.security.crypto import decrypt

log = logging.getLogger(__name__)


def render_text(sig: Signal) -> tuple[str, str]:
    s = get_settings()
    a = sig.asset
    subject = f"{a.symbol} {sig.timeframe} {sig.direction} setup detected - score {sig.score:.0f}/100"
    body = (
        f"{a.symbol}\n"
        f"{sig.timeframe} {sig.direction} setup detected ({(sig.setup_type or '').replace('_', ' ')})\n"
        f"Score: {sig.score:.0f}/100\n"
        f"R:R: 1:{sig.rr_tp1:.2f}\n"
        f"Entry: {fmt(sig.entry_low)} - {fmt(sig.entry_high)}\n"
        f"Stop: {fmt(sig.stop_loss)}  TP1: {fmt(sig.tp1)}  TP2: {fmt(sig.tp2)}\n"
        f"Valid until: {sig.expires_at:%Y-%m-%d %H:%M} UTC\n"
        f"Full analysis: {s.public_app_url.rstrip('/')}/signals/{sig.id}\n\n"
        "Probabilistic analysis, not financial advice. Setups can and do fail."
    )
    return subject, body


async def send_telegram(chat_id: str, text: str) -> None:
    token = get_settings().telegram_bot_token
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not configured")
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"https://api.telegram.org/bot{token}/sendMessage",
                         json={"chat_id": chat_id, "text": html.escape(text), "parse_mode": "HTML",
                               "disable_web_page_preview": True})
        if r.status_code != 200:
            raise RuntimeError(f"telegram HTTP {r.status_code}: {r.text[:200]}")


async def send_email(to: str, subject: str, text: str) -> None:
    s = get_settings()
    if not s.smtp_host or not s.smtp_from:
        raise RuntimeError("SMTP not configured")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to, subject
    msg.set_content(text)
    await aiosmtplib.send(msg, hostname=s.smtp_host, port=s.smtp_port, username=s.smtp_username or None,
                          password=s.smtp_password or None, start_tls=s.smtp_starttls, timeout=15)


async def _watchlist(session: AsyncSession, user_id: int) -> set[int]:
    return set((await session.execute(select(WatchlistItem.asset_id).where(WatchlistItem.user_id == user_id))).scalars())


async def dispatch(session: AsyncSession, sig: Signal) -> int:
    if sig.status != "ACTIONABLE" or not await flags.get_flag("alerts_enabled", session):
        return 0
    rules = (await session.execute(select(AlertRule).where(AlertRule.is_enabled.is_(True), AlertRule.min_score <= sig.score))).scalars().all()
    sent = 0
    subject, text = render_text(sig)
    sig_id, sig_tf, sig_asset = sig.id, sig.timeframe, sig.asset_id
    for rule in rules:
        if rule.timeframes and sig_tf not in rule.timeframes:
            continue
        assets = set(rule.asset_ids) if rule.asset_ids else await _watchlist(session, rule.user_id)
        if sig_asset not in assets:
            continue
        delivery = AlertDelivery(alert_id=rule.id, signal_id=sig_id, status="pending")
        try:
            async with session.begin_nested():  # savepoint: a duplicate does not roll back the session
                session.add(delivery)
                await session.flush()
        except IntegrityError:
            continue  # already delivered for this (rule, signal)
        try:
            if rule.channel == "telegram":
                await send_telegram(decrypt(rule.destination_encrypted or ""), text)
            elif rule.channel == "email":
                await send_email(decrypt(rule.destination_encrypted or ""), subject, text)
            elif rule.channel == "browser":
                await bus.publish(CH_SIGNALS, {"type": "notify", "user_id": rule.user_id, "signal_id": sig_id,
                                               "title": subject, "body": text.split("\n\n")[0]})
            delivery.status = "sent"
            sent += 1
        except Exception as exc:  # noqa: BLE001
            delivery.status, delivery.error = "failed", str(exc)[:500]
            log.warning("alert %s via %s failed: %s", rule.id, rule.channel, exc)
        await session.commit()
    return sent
