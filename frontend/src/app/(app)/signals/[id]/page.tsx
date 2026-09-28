"use client";

import clsx from "clsx";
import { ArrowLeft, CheckCircle2, ShieldAlert, XCircle } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import { PriceChart } from "@/components/PriceChart";
import { OutcomeBadge } from "@/components/signals";
import {
  Button,
  Card,
  CardHeader,
  ErrorNote,
  Field,
  Input,
  Notice,
  RegimePill,
  ScoreRing,
  Skeleton,
  StateBadge,
  TableWrap,
  signalKind,
  td,
  th,
} from "@/components/ui";
import { api } from "@/lib/api";
import { COMPONENT_LABEL, dateTime, num, price, r, ratio, setupLabel } from "@/lib/format";
import type { Signal } from "@/lib/types";

const TF_SEC: Record<string, number> = { "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400 };

/** Window the chart on the signal: history before it plus the bars used to resolve its outcome. */
function chartUntil(s: Signal): number | undefined {
  const bar = Math.floor(new Date(s.bar_time).getTime() / 1000);
  const after = ((s.levels_meta?.sim?.expiry_bars as number) ?? 4) + ((s.levels_meta?.sim?.max_hold_bars as number) ?? 48);
  const end = bar + after * TF_SEC[s.timeframe];
  return end < Date.now() / 1000 - TF_SEC[s.timeframe] ? end : undefined;
}

function Level({ label, value, tone, sub }: { label: string; value: string; tone?: "buy" | "sell" | "accent"; sub?: string }) {
  return (
    <div className="rounded-lg border border-line bg-surface-2/50 px-3 py-2.5">
      <div className="text-[11px] font-medium uppercase tracking-wider text-subtle">{label}</div>
      <div className={clsx("num mt-1 text-[15px] font-semibold", tone === "buy" && "text-buy", tone === "sell" && "text-sell", tone === "accent" && "text-accent")}>{value}</div>
      {sub && <div className="mt-0.5 text-[11px] leading-snug text-muted">{sub}</div>}
    </div>
  );
}

function PaperTicket({ s }: { s: Signal }) {
  const router = useRouter();
  const [risk, setRisk] = useState("1");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  async function open() {
    setBusy(true);
    setErr(null);
    try {
      await api("/api/paper/trades", {
        method: "POST",
        json: { asset_id: s.asset.id, direction: s.direction, risk_pct: Number(risk), stop_loss: s.stop_loss, take_profit: s.tp1, signal_id: s.id },
      });
      router.push("/paper");
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-3">
      <p className="text-xs leading-relaxed text-muted">
        Opens a <strong className="text-fg">simulated</strong> position at the current live price with this setup&apos;s stop and TP1, sized so a stop-out
        loses the chosen % of your virtual balance. No real order is sent.
      </p>
      <div className="flex items-end gap-2">
        <Field label="Risk per trade (%)" className="flex-1">
          <Input type="number" min={0.1} max={5} step={0.1} value={risk} onChange={(e) => setRisk(e.target.value)} />
        </Field>
        <Button variant={s.direction === "BUY" ? "buy" : "sell"} loading={busy} onClick={open}>
          Paper {s.direction}
        </Button>
      </div>
      <ErrorNote error={err} />
    </div>
  );
}

export default function SignalDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: s, error } = useSWR<Signal>(`/api/signals/${id}`, { refreshInterval: 30_000 });
  const [showFeatures, setShowFeatures] = useState(false);
  if (error) return <ErrorNote error={error} />;
  if (!s) return <Skeleton className="h-[600px]" />;
  const p = s.asset.price_precision;
  const kind = signalKind(s);
  const threshold = s.levels_meta?.min_score ?? 70;
  const actionable = s.status === "ACTIONABLE";
  const expired = s.expires_at ? new Date(s.expires_at).getTime() < Date.now() : false;
  const mtf = s.mtf?.timeframes || {};

  return (
    <div>
      <Link href="/signals" className="mb-4 inline-flex items-center gap-1 text-xs font-medium text-muted hover:text-fg">
        <ArrowLeft className="h-3.5 w-3.5" /> Signals
      </Link>

      <Card className="mb-5">
        <div className="flex flex-col gap-5 p-5 md:flex-row md:items-center">
          <ScoreRing score={s.score} threshold={threshold} size={88} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-xl font-semibold tracking-tight">{s.asset.symbol}</h1>
              <span className="rounded bg-surface-2 px-1.5 py-0.5 text-xs font-medium text-muted">{s.timeframe}</span>
              <StateBadge kind={kind} />
              <RegimePill regime={s.regime} />
              {s.setup_type && <span className="text-xs capitalize text-muted">{setupLabel(s.setup_type)}</span>}
            </div>
            <p className="mt-1.5 text-sm font-medium">{s.headline}</p>
            <p className="mt-1 text-xs text-muted">
              Candle closed {dateTime(s.bar_time)} · evaluated {dateTime(s.created_at)} · {s.strategy_version} · engine {s.engine_version}
              {s.ml_probability != null && ` · validated model P(TP1 first) ${(s.ml_probability * 100).toFixed(0)}%`}
            </p>
          </div>
          {actionable && (
            <div className="text-left md:text-right">
              <div className="text-[11px] uppercase tracking-wider text-subtle">Outcome tracking</div>
              <div className="mt-1">
                <OutcomeBadge o={s.outcome} />
              </div>
              <div className="mt-1 text-[11px] text-subtle">{expired ? "Entry window closed" : `Valid until ${dateTime(s.expires_at)}`}</div>
            </div>
          )}
        </div>
        {actionable && (
          <div className="grid grid-cols-2 gap-2 border-t border-line p-4 sm:grid-cols-3 lg:grid-cols-6">
            <Level label="Entry zone" value={`${price(s.entry_low, p)} – ${price(s.entry_high, p)}`} tone="accent" sub={s.levels_meta?.methods?.entry} />
            <Level label="Stop-loss" value={price(s.stop_loss, p)} tone="sell" sub={s.levels_meta?.methods?.stop} />
            <Level label="Take-profit 1" value={price(s.tp1, p)} tone="buy" sub={s.levels_meta?.methods?.tp1} />
            <Level label="Take-profit 2" value={price(s.tp2, p)} tone="buy" sub={s.levels_meta?.methods?.tp2} />
            <Level label="Reward : risk" value={ratio(s.rr_tp1)} sub={`TP2 ${ratio(s.rr_tp2)} · risk ${num(s.levels_meta?.risk_atr, 2)} ATR`} />
            <Level label="Expires" value={dateTime(s.expires_at)} sub={`${s.levels_meta?.sim?.expiry_bars ?? "—"} bars to fill`} />
          </div>
        )}
      </Card>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-3">
        <div className="space-y-5 xl:col-span-2">
          <Card>
            <CardHeader title="Chart" subtitle="Entry, stop, targets and nearby support/resistance zones from the analysis" />
            <div className="p-2">
              <PriceChart assetId={s.asset.id} tf={s.timeframe} signal={s} height={440} until={chartUntil(s)} limit={220} />
            </div>
          </Card>

          <Card>
            <CardHeader title="Explanation" subtitle="Generated from the computed features below — not free-form text" />
            <div className="space-y-4 p-4">
              <p className="text-sm leading-relaxed">{s.explanation}</p>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <div>
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-subtle">{actionable ? "Why" : "Context"}</h3>
                  <ul className="space-y-1.5">
                    {(s.reasons || []).map((x) => (
                      <li key={x} className="flex gap-2 text-sm">
                        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-buy" aria-hidden />
                        <span>{x}</span>
                      </li>
                    ))}
                    {(s.reasons || []).length === 0 && <li className="text-sm text-muted">No supporting evidence met the bar for a trade.</li>}
                  </ul>
                </div>
                <div>
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-subtle">Against / risks</h3>
                  <ul className="space-y-1.5">
                    {(s.risks || []).map((x) => (
                      <li key={x} className="flex gap-2 text-sm">
                        <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-sell" aria-hidden />
                        <span>{x}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
              {(s.invalidation || []).length > 0 && (
                <div>
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-subtle">Invalidation</h3>
                  <ul className="space-y-1.5">
                    {s.invalidation!.map((x) => (
                      <li key={x} className="flex gap-2 text-sm">
                        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-wait" aria-hidden />
                        <span>{x}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <Notice>This is a probabilistic assessment of a historical pattern, not a prediction or financial advice. Setups with high scores still lose.</Notice>
            </div>
          </Card>
        </div>

        <div className="space-y-5">
          <Card>
            <CardHeader title="Score breakdown" subtitle={`Threshold ${threshold}. Weights are configurable per strategy version.`} />
            <div className="divide-y divide-line">
              {(s.components || []).length === 0 && <p className="p-4 text-sm text-muted">No candidate setup was scored for this candle.</p>}
              {(s.components || []).map((c) => (
                <div key={c.name} className="px-4 py-3">
                  <div className="flex items-center justify-between text-sm">
                    <span className="font-medium">{COMPONENT_LABEL[c.name] || c.name}</span>
                    <span className="num text-xs text-muted">{c.available ? `${num(c.points, 1)} / ${c.weight}` : "n/a"}</span>
                  </div>
                  <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface-3" aria-hidden>
                    <div className="h-full rounded-full bg-accent" style={{ width: `${c.available ? c.score * 100 : 0}%` }} />
                  </div>
                  {[...c.evidence.slice(0, 3)].map((e) => (
                    <div key={e} className="mt-1.5 text-[11px] leading-snug text-muted">
                      + {e}
                    </div>
                  ))}
                  {c.against.slice(0, 2).map((e) => (
                    <div key={e} className="mt-1 text-[11px] leading-snug text-subtle">
                      − {e}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </Card>

          <Card>
            <CardHeader
              title="Multi-timeframe"
              subtitle={s.mtf?.agreement != null ? `Weighted agreement ${num(s.mtf.agreement, 2)} · coverage ${num((s.mtf.coverage ?? 1) * 100, 0)}%` : "Bias per timeframe at the signal time"}
            />
            <TableWrap>
              <table className="w-full">
                <thead className="border-b border-line">
                  <tr>
                    <th className={th}>TF</th>
                    <th className={th}>Bias</th>
                    <th className={th}>Regime</th>
                    <th className={clsx(th, "text-right")}>Score</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {["5m", "15m", "1h", "4h", "1d"].map((t) => {
                    const x = mtf[t];
                    return (
                      <tr key={t} className={t === s.timeframe ? "bg-accent-soft/40" : undefined}>
                        <td className={clsx(td, "text-xs font-medium")}>{t}</td>
                        <td className={clsx(td, "text-xs capitalize", x?.bias === "bullish" ? "text-buy" : x?.bias === "bearish" ? "text-sell" : "text-muted")}>
                          {x?.available ? x.detail || x.bias : "unavailable"}
                        </td>
                        <td className={td}>{x?.regime ? <RegimePill regime={x.regime} /> : <span className="text-xs text-subtle">—</span>}</td>
                        <td className={clsx(td, "num text-right text-xs")}>{x?.score != null ? num(x.score, 2) : "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableWrap>
          </Card>

          {actionable && !expired && (
            <Card>
              <CardHeader title="Paper trade this setup" />
              <div className="p-4">
                <PaperTicket s={s} />
              </div>
            </Card>
          )}

          {s.outcome && (s.outcome.detail?.legs || []).length > 0 && (
            <Card>
              <CardHeader title="Outcome detail" subtitle={s.outcome.detail?.basis} />
              <ul className="divide-y divide-line text-xs">
                {s.outcome.detail!.legs!.map((l, i) => (
                  <li key={i} className="flex justify-between px-4 py-2">
                    <span className="capitalize text-muted">
                      {l.reason} · {(l.fraction * 100).toFixed(0)}%
                    </span>
                    <span className="num">{price(l.price, p)}</span>
                  </li>
                ))}
                <li className="flex justify-between px-4 py-2 font-medium">
                  <span>Result</span>
                  <span className="num">{r(s.outcome.r_multiple)}</span>
                </li>
              </ul>
            </Card>
          )}

          <Card>
            <CardHeader
              title="Data quality & features"
              action={
                <Button size="sm" variant="ghost" onClick={() => setShowFeatures(!showFeatures)}>
                  {showFeatures ? "Hide" : "Show"} features
                </Button>
              }
            />
            <div className="space-y-2 p-4 text-xs">
              <div className="text-muted">
                {s.data_quality?.bars != null ? `${s.data_quality.bars} closed candles checked` : "Quality report unavailable"}
                {s.data_quality?.ok === false && " — failed"}
              </div>
              {(s.data_quality?.issues || []).map((i) => (
                <div key={i} className="text-sell">
                  {i}
                </div>
              ))}
              {(s.data_quality?.warnings || []).map((i) => (
                <div key={i} className="text-wait">
                  {i}
                </div>
              ))}
              {showFeatures && (
                <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 border-t border-line pt-3">
                  {Object.entries(s.features || {}).map(([k, v]) => (
                    <div key={k} className="flex justify-between gap-2">
                      <span className="truncate text-subtle">{k}</span>
                      <span className="num">{v == null ? "—" : num(v, 4)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
