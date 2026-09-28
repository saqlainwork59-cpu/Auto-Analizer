"use client";

import clsx from "clsx";
import { FlaskConical, Trash2 } from "lucide-react";
import { useState } from "react";
import useSWR, { mutate } from "swr";
import { TimeLineChart } from "@/components/LineChart";
import { useMarkets } from "@/components/MarketBits";
import {
  Button,
  Card,
  CardHeader,
  Empty,
  ErrorNote,
  Field,
  Input,
  Notice,
  PageHeader,
  Select,
  Skeleton,
  Stat,
  TableWrap,
  td,
  th,
} from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime, money, num, pct, price, r, setupLabel } from "@/lib/format";

interface BtSummary {
  id: number;
  status: string;
  progress: number;
  params: Record<string, string | number | Record<string, unknown>>;
  created_at: string;
  error: string | null;
  summary: Record<string, number | string | null> | null;
}
interface Breakdown {
  key: string;
  trades: number;
  win_rate: number;
  avg_r: number;
  total_r: number;
  profit_factor: number | string | null;
}
interface BtFull extends BtSummary {
  metrics: Record<string, number | string | boolean | null> & { breakdowns?: Record<string, Breakdown[]> };
  equity_curve: { time: string; equity: number }[];
  data_summary: { assumptions: string[]; notes?: string[]; evaluated_from: string; evaluated_to: string; first_candle_by_timeframe?: Record<string, string | null> };
  trades: {
    id: number; direction: string; setup_type: string; regime: string; score: number; signal_time: string; entry_time: string; exit_time: string;
    entry_price: number; exit_price: number; stop_loss: number; outcome: string; r_multiple: number; pnl: number; fees: number;
  }[];
}

const today = new Date();
const iso = (d: Date) => d.toISOString().slice(0, 10);

function pf(v: unknown) {
  if (v === "inf") return "∞";
  return typeof v === "number" ? v.toFixed(2) : "—";
}

function Form({ onCreated }: { onCreated: (id: number) => void }) {
  const { data: mk } = useMarkets();
  const [f, setF] = useState({
    asset_id: "",
    timeframe: "1h",
    start: iso(new Date(today.getTime() - 365 * 86400e3)),
    end: iso(today),
    setups: "all",
    min_score: "",
    starting_capital: "10000",
    risk_per_trade_pct: "1",
    fee_pct: "0.1",
    slippage_pct: "0.05",
  });
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const fallback = mk?.markets.find((m) => m.data_status !== "unavailable") ?? mk?.markets[0];
  const assetValue = f.asset_id || (fallback ? String(fallback.id) : "");
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const res = await api<{ id: number }>("/api/backtests", {
        method: "POST",
        json: {
          asset_id: Number(assetValue),
          timeframe: f.timeframe,
          start: `${f.start}T00:00:00Z`,
          end: `${f.end}T23:59:59Z`,
          setups: f.setups === "all" ? null : [f.setups],
          min_score: f.min_score ? Number(f.min_score) : null,
          starting_capital: Number(f.starting_capital),
          risk_per_trade_pct: Number(f.risk_per_trade_pct),
          fee_pct: Number(f.fee_pct),
          slippage_pct: Number(f.slippage_pct),
        },
      });
      await mutate("/api/backtests");
      onCreated(res.id);
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="grid grid-cols-2 gap-3 p-4">
      <Field label="Market" className="col-span-2">
        <Select value={assetValue} onChange={set("asset_id")}>
          {mk?.markets.map((m) => (
            <option key={m.id} value={m.id}>
              {m.symbol} — {m.name}
              {m.data_status === "unavailable" ? " (data unavailable)" : ""}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Timeframe">
        <Select value={f.timeframe} onChange={set("timeframe")}>
          {["15m", "1h", "4h", "1d"].map((t) => (
            <option key={t}>{t}</option>
          ))}
        </Select>
      </Field>
      <Field label="Strategy">
        <Select value={f.setups} onChange={set("setups")}>
          <option value="all">Multi-factor (all playbooks)</option>
          <option value="trend_pullback">Trend pullback only</option>
          <option value="breakout_retest">Breakout retest only</option>
          <option value="range_reversion">Range reversion only</option>
        </Select>
      </Field>
      <Field label="From">
        <Input type="date" value={f.start} onChange={set("start")} max={f.end} />
      </Field>
      <Field label="To">
        <Input type="date" value={f.end} onChange={set("end")} max={iso(today)} />
      </Field>
      <Field label="Starting capital ($)">
        <Input type="number" min={100} value={f.starting_capital} onChange={set("starting_capital")} />
      </Field>
      <Field label="Risk per trade (%)">
        <Input type="number" min={0.1} max={10} step={0.1} value={f.risk_per_trade_pct} onChange={set("risk_per_trade_pct")} />
      </Field>
      <Field label="Fee per side (%)">
        <Input type="number" min={0} max={2} step={0.01} value={f.fee_pct} onChange={set("fee_pct")} />
      </Field>
      <Field label="Slippage (%)">
        <Input type="number" min={0} max={2} step={0.01} value={f.slippage_pct} onChange={set("slippage_pct")} />
      </Field>
      <Field label="Min score override" hint="Blank = active strategy threshold" className="col-span-2">
        <Input type="number" min={0} max={100} value={f.min_score} onChange={set("min_score")} placeholder="e.g. 75" />
      </Field>
      <div className="col-span-2">
        <ErrorNote error={err} />
      </div>
      <Button variant="primary" className="col-span-2" type="submit" loading={busy}>
        <FlaskConical className="h-4 w-4" /> Run backtest
      </Button>
      <p className="col-span-2 text-[11px] leading-relaxed text-subtle">
        Uses the same engine as live signals, bar by bar on closed candles only. Missing history is downloaded from the provider when possible.
      </p>
    </form>
  );
}

function Result({ id }: { id: number }) {
  const { data: b } = useSWR<BtFull>(`/api/backtests/${id}`, {
    refreshInterval: (d) => (d && (d.status === "done" || d.status === "failed") ? 0 : 1500),
  });
  const [showAll, setShowAll] = useState(false);
  if (!b) return <Skeleton className="h-96" />;
  if (b.status === "queued" || b.status === "running")
    return (
      <Card className="p-6">
        <div className="text-sm font-medium">{b.status === "queued" ? "Queued…" : "Running backtest…"}</div>
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-surface-3">
          <div className="h-full bg-accent transition-all" style={{ width: `${Math.round(b.progress * 100)}%` }} />
        </div>
        <div className="num mt-2 text-xs text-muted">{Math.round(b.progress * 100)}%</div>
      </Card>
    );
  if (b.status === "failed") return <ErrorNote error={new Error(b.error || "Backtest failed")} />;
  const m = b.metrics;
  const bd = m.breakdowns || {};
  const trades = showAll ? b.trades : b.trades.slice(-50);
  return (
    <div className="space-y-5">
      <Notice tone="wait">
        Historical simulation of {String(b.params.symbol)} {String(b.params.timeframe)} from {dateTime(b.data_summary.evaluated_from)} to {dateTime(b.data_summary.evaluated_to)}.
        Past results do not guarantee or predict future performance.{m.sample_size_warning ? " Fewer than 30 trades: statistics are not reliable." : ""}
      </Notice>
      <Card>
        <div className="grid grid-cols-2 gap-5 p-5 sm:grid-cols-3 lg:grid-cols-5">
          <Stat label="Net return" value={pct(m.net_return_pct as number)} tone={(m.net_return_pct as number) >= 0 ? "buy" : "sell"} sub={money(m.net_profit as number, true)} />
          <Stat label="Total trades" value={num(m.total_trades as number, 0)} sub={`${m.winning_trades} won · ${m.losing_trades} lost`} />
          <Stat label="Win rate" value={m.win_rate == null ? "—" : pct((m.win_rate as number) * 100, 1, false)} />
          <Stat label="Profit factor" value={pf(m.profit_factor)} />
          <Stat label="Max drawdown" value={pct(m.max_drawdown_pct as number, 2, false)} tone="sell" sub={money(m.max_drawdown_abs as number)} />
          <Stat label="Avg planned R:R" value={m.avg_planned_rr == null ? "—" : `1:${(m.avg_planned_rr as number).toFixed(2)}`} />
          <Stat label="Expectancy" value={r(m.expectancy_r as number)} sub="net, per trade" />
          <Stat label="Sharpe (daily)" value={m.sharpe_ratio == null ? "n/a" : num(m.sharpe_ratio as number, 2)} sub={m.sharpe_ratio == null ? "needs ≥ 30 days" : "annualised, rf = 0"} />
          <Stat label="Average trade" value={money(m.avg_trade as number, true)} />
          <Stat label="Longest losing streak" value={num(m.longest_losing_streak as number, 0)} sub={`${m.unfilled_signals} signals never filled`} />
        </div>
      </Card>
      <Card>
        <CardHeader title="Equity curve" subtitle="Realised equity after each closed trade (net of fees & slippage)" />
        <div className="p-2">
          <TimeLineChart points={b.equity_curve.map((p) => ({ time: p.time, value: p.equity }))} format={(v) => money(v)} baseline={Number(b.params.starting_capital)} height={280} />
        </div>
      </Card>
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        {(["by_setup", "by_regime", "by_direction"] as const).map((k) => (
          <Card key={k}>
            <CardHeader title={k.replace("by_", "By ").replace("_", " ")} />
            <TableWrap>
              <table className="w-full">
                <thead className="border-b border-line">
                  <tr>
                    <th className={th}>Group</th>
                    <th className={clsx(th, "text-right")}>Trades</th>
                    <th className={clsx(th, "text-right")}>Win %</th>
                    <th className={clsx(th, "text-right")}>Avg R</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {(bd[k] || []).map((g) => (
                    <tr key={g.key}>
                      <td className={clsx(td, "text-xs capitalize")}>{setupLabel(g.key).toLowerCase()}</td>
                      <td className={clsx(td, "num text-right text-xs")}>{g.trades}</td>
                      <td className={clsx(td, "num text-right text-xs")}>{(g.win_rate * 100).toFixed(0)}%</td>
                      <td className={clsx(td, "num text-right text-xs", g.avg_r >= 0 ? "text-buy" : "text-sell")}>{r(g.avg_r)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          </Card>
        ))}
      </div>
      <Card>
        <CardHeader
          title="Trades"
          subtitle={`${b.trades.length} trades${!showAll && b.trades.length > 50 ? " · showing the last 50" : ""}`}
          action={
            b.trades.length > 50 && (
              <Button size="sm" variant="ghost" onClick={() => setShowAll(!showAll)}>
                {showAll ? "Show last 50" : "Show all"}
              </Button>
            )
          }
        />
        <TableWrap>
          <table className="w-full min-w-[860px]">
            <thead className="border-b border-line">
              <tr>
                {["Entry", "Exit", "Side", "Setup", "Score", "Entry px", "Exit px", "Outcome", "R", "P&L"].map((h) => (
                  <th key={h} className={th}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {trades.map((t) => (
                <tr key={t.id}>
                  <td className={clsx(td, "text-xs text-muted")}>{dateTime(t.entry_time)}</td>
                  <td className={clsx(td, "text-xs text-muted")}>{dateTime(t.exit_time)}</td>
                  <td className={clsx(td, "text-xs font-semibold", t.direction === "BUY" ? "text-buy" : "text-sell")}>{t.direction}</td>
                  <td className={clsx(td, "text-xs capitalize")}>{setupLabel(t.setup_type)}</td>
                  <td className={clsx(td, "num text-xs")}>{t.score.toFixed(0)}</td>
                  <td className={clsx(td, "num text-xs")}>{price(t.entry_price, 4)}</td>
                  <td className={clsx(td, "num text-xs")}>{price(t.exit_price, 4)}</td>
                  <td className={clsx(td, "text-xs")}>{t.outcome.replace("_", " ")}</td>
                  <td className={clsx(td, "num text-xs", t.r_multiple >= 0 ? "text-buy" : "text-sell")}>{r(t.r_multiple)}</td>
                  <td className={clsx(td, "num text-xs", t.pnl >= 0 ? "text-buy" : "text-sell")}>{money(t.pnl, true)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      </Card>
      <Card>
        <CardHeader title="Methodology & assumptions" />
        <ul className="list-disc space-y-1 px-8 py-4 text-xs leading-relaxed text-muted">
          {b.data_summary.assumptions.map((a) => (
            <li key={a}>{a}</li>
          ))}
          {(b.data_summary.notes || []).map((a) => (
            <li key={a} className="text-wait">
              {a}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

export default function BacktestingPage() {
  const { data: list } = useSWR<BtSummary[]>("/api/backtests", { refreshInterval: 5000 });
  const [sel, setSel] = useState<number | null>(null);
  const current = sel ?? list?.[0]?.id ?? null;
  async function del(id: number) {
    await api(`/api/backtests/${id}`, { method: "DELETE" });
    if (sel === id) setSel(null);
    mutate("/api/backtests");
  }
  return (
    <div>
      <PageHeader title="Backtesting" subtitle="Replay the signal engine over history with realistic fills, fees, slippage and risk-based sizing." />
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[360px_minmax(0,1fr)]">
        <div className="space-y-5">
          <Card>
            <CardHeader title="New backtest" />
            <Form onCreated={setSel} />
          </Card>
          <Card>
            <CardHeader title="Runs" />
            <ul className="divide-y divide-line">
              {(list || []).map((b) => (
                <li key={b.id} className={clsx("flex items-center gap-2 px-3 py-2", current === b.id && "bg-surface-2")}>
                  <button onClick={() => setSel(b.id)} className="min-w-0 flex-1 text-left">
                    <div className="truncate text-xs font-medium">
                      #{b.id} {String(b.params.symbol)} · {String(b.params.timeframe)}
                    </div>
                    <div className="text-[11px] text-subtle">
                      {b.status === "done" && b.summary
                        ? `${b.summary.total_trades} trades · ${pct(b.summary.net_return_pct as number)}`
                        : b.status}
                    </div>
                  </button>
                  <button onClick={() => del(b.id)} aria-label={`Delete backtest ${b.id}`} className="rounded p-1 text-subtle hover:text-sell">
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </li>
              ))}
              {list && list.length === 0 && <li className="px-4 py-3 text-xs text-subtle">No runs yet.</li>}
            </ul>
          </Card>
        </div>
        <div className="min-w-0">
          {current ? <Result id={current} /> : <Card><Empty icon={<FlaskConical className="h-6 w-6" />} title="No backtest selected" body="Configure a run on the left. Results are labelled as historical and never presented as expected returns." /></Card>}
        </div>
      </div>
    </div>
  );
}
