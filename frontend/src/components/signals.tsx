"use client";

import clsx from "clsx";
import { ArrowRight, Clock } from "lucide-react";
import Link from "next/link";
import { ago, dateTime, price, r, ratio, regimeLabel, setupLabel } from "@/lib/format";
import type { Signal } from "@/lib/types";
import { RegimePill, ScoreBar, StateBadge, TableWrap, signalKind, td, th } from "./ui";

export function OutcomeBadge({ o }: { o: Signal["outcome"] }) {
  if (!o) return <span className="text-xs text-subtle">—</span>;
  const tone =
    o.status.startsWith("WIN") ? "text-buy bg-buy-soft" : o.status === "LOSS" ? "text-sell bg-sell-soft" : o.status === "OPEN" || o.status === "PENDING" ? "text-accent bg-accent-soft" : "text-muted bg-surface-2";
  const label = { WIN_TP1: "TP1 hit", WIN_TP2: "TP2 hit", LOSS: "Stopped out", PENDING: "Awaiting entry", OPEN: "In trade", EXPIRED: "Expired", INVALIDATED: "Invalidated", TIME_EXIT: "Time exit", BREAKEVEN: "Breakeven" }[o.status] || o.status;
  return (
    <span className={clsx("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium", tone)}>
      {label}
      {o.r_multiple != null && <span className="num">{r(o.r_multiple)}</span>}
    </span>
  );
}

export function SetupCard({ s }: { s: Signal }) {
  const p = s.asset.price_precision;
  return (
    <Link
      href={`/signals/${s.id}`}
      className="group block rounded-xl border border-line bg-surface p-4 transition-colors hover:border-line-strong hover:bg-surface-2/40"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-semibold">{s.asset.symbol}</span>
            <span className="rounded bg-surface-2 px-1.5 py-0.5 text-[10px] font-medium text-muted">{s.timeframe}</span>
          </div>
          <div className="mt-0.5 truncate text-xs capitalize text-muted">{setupLabel(s.setup_type)}</div>
        </div>
        <StateBadge kind={signalKind(s)} size="sm" />
      </div>
      <dl className="mt-3 grid grid-cols-3 gap-2 text-[11px]">
        <div>
          <dt className="text-subtle">Entry</dt>
          <dd className="num mt-0.5 text-fg">{price(s.entry_ref, p)}</dd>
        </div>
        <div>
          <dt className="text-subtle">Stop</dt>
          <dd className="num mt-0.5 text-sell">{price(s.stop_loss, p)}</dd>
        </div>
        <div>
          <dt className="text-subtle">TP1</dt>
          <dd className="num mt-0.5 text-buy">{price(s.tp1, p)}</dd>
        </div>
      </dl>
      <div className="mt-3 flex items-center justify-between border-t border-line pt-3">
        <ScoreBar score={s.score} />
        <span className="num text-xs text-muted">R:R {ratio(s.rr_tp1)}</span>
      </div>
      <div className="mt-2 flex items-center justify-between text-[11px] text-subtle">
        <span className="inline-flex items-center gap-1">
          <Clock className="h-3 w-3" aria-hidden /> {ago(s.bar_time)}
        </span>
        <span className="inline-flex items-center gap-0.5 text-muted group-hover:text-accent">
          Analysis <ArrowRight className="h-3 w-3" aria-hidden />
        </span>
      </div>
    </Link>
  );
}

export function SignalTable({ items, showOutcome = true, compact = false }: { items: Signal[]; showOutcome?: boolean; compact?: boolean }) {
  return (
    <TableWrap>
      <table className="w-full min-w-[720px]">
        <thead className="border-b border-line">
          <tr>
            <th className={th}>Time</th>
            <th className={th}>Market</th>
            <th className={th}>Signal</th>
            {!compact && <th className={th}>Setup</th>}
            <th className={th}>Regime</th>
            <th className={th}>Score</th>
            <th className={clsx(th, "text-right")}>Entry</th>
            <th className={clsx(th, "text-right")}>R:R</th>
            {showOutcome && <th className={th}>Outcome</th>}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {items.map((s) => (
            <tr key={s.id} className="hover:bg-surface-2/50">
              <td className={clsx(td, "text-xs text-muted")}>
                <Link href={`/signals/${s.id}`} className="hover:text-accent">
                  {dateTime(s.bar_time)}
                </Link>
              </td>
              <td className={td}>
                <Link href={`/signals/${s.id}`} className="font-medium hover:text-accent">
                  {s.asset.symbol}
                </Link>{" "}
                <span className="text-xs text-subtle">{s.timeframe}</span>
              </td>
              <td className={td}>
                <StateBadge kind={signalKind(s)} size="sm" />
              </td>
              {!compact && <td className={clsx(td, "text-xs capitalize text-muted")}>{setupLabel(s.setup_type)}</td>}
              <td className={td}>
                <RegimePill regime={s.regime} />
              </td>
              <td className={td}>{s.status === "DATA_UNAVAILABLE" ? <span className="text-xs text-subtle">—</span> : <ScoreBar score={s.score} />}</td>
              <td className={clsx(td, "num text-right text-xs")}>{price(s.entry_ref, s.asset.price_precision)}</td>
              <td className={clsx(td, "num text-right text-xs")}>{s.status === "ACTIONABLE" ? ratio(s.rr_tp1) : "—"}</td>
              {showOutcome && (
                <td className={td}>
                  <OutcomeBadge o={s.outcome} />
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

export { regimeLabel };
