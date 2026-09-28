"use client";

import { Radar, Wallet } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { MarketTile, useMarkets, useWatchlist } from "@/components/MarketBits";
import { PriceChart } from "@/components/PriceChart";
import { SetupCard, SignalTable } from "@/components/signals";
import {
  Card,
  CardHeader,
  Delta,
  Empty,
  PageHeader,
  RegimePill,
  Segmented,
  Select,
  Skeleton,
  StateBadge,
  Stat,
  TableWrap,
  signalKind,
  td,
  th,
} from "@/components/ui";
import { money, pct } from "@/lib/format";
import type { Signal, TF } from "@/lib/types";

export default function Dashboard() {
  const { data: mk } = useMarkets();
  const { data: wl } = useWatchlist();
  const { data: top } = useSWR<{ items: Signal[] }>("/api/signals/top?limit=6", { refreshInterval: 30_000 });
  const { data: recent } = useSWR<{ items: Signal[] }>("/api/signals?status=ACTIONABLE&limit=8", { refreshInterval: 30_000 });
  const { data: paper } = useSWR<{ equity: number | null; equity_partial: number; account: { starting_balance: number; realized_pnl: number; max_drawdown_pct: number }; positions: unknown[]; current_drawdown_pct: number | null; priced_all: boolean }>("/api/paper", { refreshInterval: 15_000 });
  const { data: feeds } = useSWR<{ id: string; status: string; last_error: string | null }[]>("/api/markets/feeds", { refreshInterval: 30_000 });

  const markets = useMemo(() => mk?.markets || [], [mk]);
  const watchIds = new Set((wl || []).map((w) => w.asset_id));
  const strip = (watchIds.size ? markets.filter((m) => watchIds.has(m.id)) : markets).slice(0, 8);
  const [assetId, setAssetId] = useState<number | null>(null);
  const [tf, setTf] = useState<TF>("1h");
  useEffect(() => {
    if (assetId === null && markets.length) {
      const pick = markets.find((m) => watchIds.has(m.id) && m.data_status !== "unavailable") || markets.find((m) => m.data_status !== "unavailable") || markets[0];
      setAssetId(pick.id);
    }
  }, [markets]); // eslint-disable-line react-hooks/exhaustive-deps
  const { data: latest } = useSWR<{ signal: Signal | null }>(assetId ? `/api/markets/${assetId}/analysis?tf=${tf}` : null, { refreshInterval: 30_000 });
  const regimeRows = (watchIds.size ? markets.filter((m) => watchIds.has(m.id)) : markets).slice(0, 10);
  const tfs = mk?.analysis_timeframes || ["15m", "1h", "4h"];
  const pnl = paper ? (paper.equity ?? paper.equity_partial) - paper.account.starting_balance : null;

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle="Live markets, the highest-scoring open setups and your simulated portfolio. Scores are evidence, not certainty."
      />

      <div className="scrollbar-thin -mx-1 mb-5 flex gap-3 overflow-x-auto px-1 pb-1">
        {mk ? strip.map((m) => <MarketTile key={m.id} m={m} />) : Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-[78px] min-w-[164px] flex-1" />)}
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader
            title="Live chart"
            subtitle={latest?.signal ? latest.signal.headline : "Latest closed-candle analysis appears here"}
            action={
              <div className="flex items-center gap-2">
                <Select value={assetId ?? ""} onChange={(e) => setAssetId(Number(e.target.value))} className="h-8 w-32 text-xs" aria-label="Market">
                  {markets.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.symbol}
                    </option>
                  ))}
                </Select>
                <Segmented size="sm" value={tf} onChange={setTf} options={(["15m", "1h", "4h", "1d"] as TF[]).map((t) => ({ value: t, label: t }))} />
              </div>
            }
          />
          <div className="p-2">{assetId ? <PriceChart assetId={assetId} tf={tf} height={420} signal={latest?.signal} indicators={{ rsi: false }} /> : <Skeleton className="h-[420px]" />}</div>
          {latest?.signal && (
            <div className="flex flex-wrap items-center gap-3 border-t border-line px-4 py-2.5 text-xs text-muted">
              <StateBadge kind={signalKind(latest.signal)} size="sm" />
              <RegimePill regime={latest.signal.regime} />
              <span className="min-w-0 flex-1 truncate">{latest.signal.explanation}</span>
              <Link href={`/signals/${latest.signal.id}`} className="font-medium text-accent hover:underline">
                Full analysis
              </Link>
            </div>
          )}
        </Card>

        <Card>
          <CardHeader title="Top AI setups" subtitle="Open, unexpired setups ranked by score" action={<Link href="/signals" className="text-xs font-medium text-accent hover:underline">All signals</Link>} />
          <div className="space-y-3 p-3">
            {!top && Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-[150px]" />)}
            {top && top.items.length === 0 && (
              <Empty
                icon={<Radar className="h-6 w-6" />}
                title="WAIT — No high-quality setup detected"
                body="No market currently has an open setup that clears the score, reward/risk and multi-timeframe gates. Standing aside is a position."
              />
            )}
            {top?.items.map((s) => <SetupCard key={s.id} s={s} />)}
          </div>
        </Card>
      </div>

      <div className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-2 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Market regime" subtitle="Latest closed-candle evaluation per timeframe" action={<Link href="/watchlist" className="text-xs font-medium text-accent hover:underline">Watchlist</Link>} />
          <TableWrap>
            <table className="w-full min-w-[560px]">
              <thead className="border-b border-line">
                <tr>
                  <th className={th}>Market</th>
                  {tfs.map((t) => (
                    <th key={t} className={th}>
                      {t}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {regimeRows.map((m) => (
                  <tr key={m.id}>
                    <td className={td}>
                      <Link href={`/charts?asset=${m.id}`} className="font-medium hover:text-accent">
                        {m.symbol}
                      </Link>
                    </td>
                    {tfs.map((t) => {
                      const a = m.analysis[t];
                      return (
                        <td key={t} className={td}>
                          {m.data_status === "unavailable" && !a ? (
                            <StateBadge kind="DATA_UNAVAILABLE" size="sm" />
                          ) : a ? (
                            <Link href={`/signals/${a.id}`} className="flex items-center gap-1.5">
                              <StateBadge kind={signalKind(a)} size="sm" />
                              {a.status !== "DATA_UNAVAILABLE" && <RegimePill regime={a.regime} />}
                            </Link>
                          ) : (
                            <StateBadge kind="ANALYZING" size="sm" />
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </Card>

        <div className="space-y-5">
          <Card>
            <CardHeader title="Paper portfolio" subtitle="Simulated funds — separate from real money" action={<Link href="/paper" className="text-xs font-medium text-accent hover:underline">Open</Link>} />
            {paper ? (
              <div className="grid grid-cols-2 gap-4 p-4">
                <Stat label="Equity" value={paper.equity == null ? "—" : money(paper.equity)} sub={paper.priced_all ? "marked to live prices" : "some prices unavailable"} />
                <Stat label="P&L" value={<Delta v={pnl}>{money(pnl, true)}</Delta>} sub={`from ${money(paper.account.starting_balance)}`} />
                <Stat label="Open positions" value={paper.positions.length} />
                <Stat label="Max drawdown" value={pct(paper.account.max_drawdown_pct, 2, false)} />
              </div>
            ) : (
              <Skeleton className="m-4 h-24" />
            )}
          </Card>
          <Card>
            <CardHeader title="Data feeds" subtitle="Provider connection health" />
            <ul className="divide-y divide-line">
              {(feeds || []).map((f) => (
                <li key={f.id} className="flex items-center justify-between gap-3 px-4 py-2 text-xs">
                  <span className="font-medium">{f.id}</span>
                  <span
                    className={
                      f.status === "ok" ? "text-buy" : f.status === "degraded" || f.status === "down" ? "text-sell" : "text-subtle"
                    }
                    title={f.last_error || undefined}
                  >
                    {f.status}
                  </span>
                </li>
              ))}
              {feeds && feeds.length === 0 && <li className="px-4 py-3 text-xs text-subtle">Worker has not reported yet.</li>}
            </ul>
          </Card>
        </div>
      </div>

      <Card className="mt-5">
        <CardHeader title="Recent signals" subtitle="Every actionable signal is tracked to its outcome — including losses" action={<Link href="/performance" className="text-xs font-medium text-accent hover:underline">Performance audit</Link>} />
        {recent && recent.items.length === 0 ? (
          <Empty icon={<Wallet className="h-6 w-6" />} title="No actionable signals yet" body="Signals appear after the engine evaluates closed candles and a setup clears every gate." />
        ) : (
          <SignalTable items={recent?.items || []} />
        )}
      </Card>
    </div>
  );
}
