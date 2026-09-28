"use client";

import clsx from "clsx";
import { LineChart } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { TimeLineChart } from "@/components/LineChart";
import { SignalTable } from "@/components/signals";
import { Card, CardHeader, Empty, PageHeader, Segmented, Skeleton, Stat, TableWrap, td, th } from "@/components/ui";
import { num, pct, r, setupLabel } from "@/lib/format";
import type { Signal } from "@/lib/types";

interface Stats {
  key?: string;
  signals: number;
  closed: number;
  open: number;
  pending: number;
  not_triggered: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  avg_r: number | null;
  total_r: number;
  avg_planned_rr: number | null;
  profit_factor: number | string | null;
}

interface Perf {
  overall: Stats;
  max_drawdown_r: number;
  equity_curve_r: { time: string; cum_r: number }[];
  evaluations_wait: number;
  by_asset: Stats[];
  by_timeframe: Stats[];
  by_regime: Stats[];
  by_setup: Stats[];
  by_direction: Stats[];
  by_outcome: Record<string, number>;
  methodology: string[];
}

function Breakdown({ title, rows }: { title: string; rows: Stats[] }) {
  return (
    <Card>
      <CardHeader title={title} />
      <TableWrap>
        <table className="w-full min-w-[420px]">
          <thead className="border-b border-line">
            <tr>
              <th className={th}>Group</th>
              <th className={clsx(th, "text-right")}>Signals</th>
              <th className={clsx(th, "text-right")}>Closed</th>
              <th className={clsx(th, "text-right")}>Win %</th>
              <th className={clsx(th, "text-right")}>Avg R</th>
              <th className={clsx(th, "text-right")}>Total R</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((g) => (
              <tr key={g.key}>
                <td className={clsx(td, "text-xs font-medium capitalize")}>{setupLabel(g.key).toLowerCase()}</td>
                <td className={clsx(td, "num text-right text-xs")}>{g.signals}</td>
                <td className={clsx(td, "num text-right text-xs")}>{g.closed}</td>
                <td className={clsx(td, "num text-right text-xs")}>{g.win_rate == null ? "—" : `${(g.win_rate * 100).toFixed(0)}%`}</td>
                <td className={clsx(td, "num text-right text-xs", (g.avg_r ?? 0) >= 0 ? "text-buy" : "text-sell")}>{r(g.avg_r)}</td>
                <td className={clsx(td, "num text-right text-xs", g.total_r >= 0 ? "text-buy" : "text-sell")}>{r(g.total_r)}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td className={clsx(td, "text-xs text-subtle")} colSpan={6}>
                  No signals in this window.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </TableWrap>
    </Card>
  );
}

export default function PerformancePage() {
  const [days, setDays] = useState("90");
  const { data: p } = useSWR<Perf>(`/api/performance?days=${days}`, { refreshInterval: 60_000 });
  const { data: losers } = useSWR<{ total: number; items: Signal[] }>("/api/signals?status=ACTIONABLE&outcome=LOSS&limit=20");
  const { data: all } = useSWR<{ total: number; items: Signal[] }>("/api/signals?status=ACTIONABLE&limit=100");
  const o = p?.overall;
  const pf = o?.profit_factor === "inf" ? "∞" : typeof o?.profit_factor === "number" ? o.profit_factor.toFixed(2) : "—";

  return (
    <div>
      <PageHeader
        title="Performance"
        subtitle="A complete audit of what the signal engine actually issued and what happened next. Losing signals are shown alongside winners."
        actions={
          <Segmented
            value={days}
            onChange={setDays}
            options={[
              { value: "30", label: "30D" },
              { value: "90", label: "90D" },
              { value: "365", label: "1Y" },
              { value: "3650", label: "All" },
            ]}
          />
        }
      />
      {!p ? (
        <Skeleton className="h-96" />
      ) : o && o.signals === 0 ? (
        <Card>
          <Empty icon={<LineChart className="h-6 w-6" />} title="No actionable signals in this window" body={`The engine recorded ${p.evaluations_wait.toLocaleString()} WAIT evaluations. Performance appears once setups are issued and resolve.`} />
        </Card>
      ) : (
        <div className="space-y-5">
          <Card>
            <div className="grid grid-cols-2 gap-5 p-5 sm:grid-cols-4 lg:grid-cols-8">
              <Stat label="Signals" value={num(o!.signals, 0)} sub={`${o!.not_triggered} never filled`} />
              <Stat label="Resolved" value={num(o!.closed, 0)} sub={`${o!.open} open · ${o!.pending} pending`} />
              <Stat label="Wins" value={num(o!.wins, 0)} tone="buy" />
              <Stat label="Losses" value={num(o!.losses, 0)} tone="sell" />
              <Stat label="Win rate" value={o!.win_rate == null ? "—" : pct(o!.win_rate * 100, 1, false)} />
              <Stat label="Avg result" value={r(o!.avg_r)} sub={`planned 1:${num(o!.avg_planned_rr, 2)}`} />
              <Stat label="Profit factor" value={pf} />
              <Stat label="Max drawdown" value={`${num(p.max_drawdown_r, 2)}R`} tone="sell" />
            </div>
          </Card>
          <Card>
            <CardHeader title="Cumulative R" subtitle="Sum of realised R multiples of resolved signals, in exit order (gross of fees)" />
            <div className="p-2">
              {p.equity_curve_r.length ? (
                <TimeLineChart points={p.equity_curve_r.map((x) => ({ time: x.time, value: x.cum_r }))} format={(v) => `${v.toFixed(2)}R`} baseline={0} />
              ) : (
                <Empty title="No resolved signals yet" />
              )}
            </div>
          </Card>
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            <Breakdown title="By market" rows={p.by_asset} />
            <Breakdown title="By timeframe" rows={p.by_timeframe} />
            <Breakdown title="By market regime" rows={p.by_regime} />
            <Breakdown title="By setup type" rows={p.by_setup} />
          </div>
          <Card>
            <CardHeader title="Outcome distribution" />
            <div className="flex flex-wrap gap-2 p-4">
              {Object.entries(p.by_outcome).map(([k, v]) => (
                <span key={k} className="rounded-md border border-line px-2 py-1 text-xs">
                  <span className="text-muted">{k.replace("_", " ")}</span> <span className="num ml-1 font-medium">{v}</span>
                </span>
              ))}
            </div>
          </Card>
          <Card>
            <CardHeader title="Losing signals" subtitle={losers ? `${losers.total} stopped out — shown, never hidden` : undefined} />
            {losers && losers.items.length ? <SignalTable items={losers.items} /> : <Empty title="No stopped-out signals recorded yet" />}
          </Card>
          <Card>
            <CardHeader title="All actionable signals" subtitle="Most recent 100" />
            <SignalTable items={all?.items || []} />
          </Card>
          <Card>
            <CardHeader title="Methodology" />
            <ul className="list-disc space-y-1 px-8 py-4 text-xs leading-relaxed text-muted">
              {p.methodology.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          </Card>
        </div>
      )}
    </div>
  );
}
