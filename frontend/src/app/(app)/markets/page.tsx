"use client";

import clsx from "clsx";
import { Search } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { LivePriceCell, MarketStatus, WatchStar, useMarkets, useWatchlist } from "@/components/MarketBits";
import { Card, Input, PageHeader, RegimePill, Segmented, Skeleton, StateBadge, TableWrap, signalKind, td, th } from "@/components/ui";
import { pct } from "@/lib/format";

const CLASSES = [
  { value: "all", label: "All" },
  { value: "crypto", label: "Crypto" },
  { value: "forex", label: "Forex" },
  { value: "stock", label: "Stocks" },
  { value: "index", label: "Indices" },
  { value: "commodity", label: "Commodities" },
];

export default function MarketsPage() {
  const { data, error } = useMarkets();
  const { data: wl } = useWatchlist();
  const [cls, setCls] = useState("all");
  const [q, setQ] = useState("");
  const watched = new Set((wl || []).map((w) => w.asset_id));
  const rows = useMemo(
    () =>
      (data?.markets || []).filter(
        (m) => (cls === "all" || m.asset_class === cls) && (!q || `${m.symbol} ${m.name}`.toLowerCase().includes(q.toLowerCase())),
      ),
    [data, cls, q],
  );
  const tfs = data?.analysis_timeframes || [];

  return (
    <div>
      <PageHeader
        title="Markets"
        subtitle="Prices come only from connected providers. When a feed is missing or stale the market says so instead of showing a number."
      />
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="scrollbar-thin overflow-x-auto">
          <Segmented value={cls} onChange={setCls} options={CLASSES} />
        </div>
        <div className="relative sm:w-64">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-subtle" aria-hidden />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search markets" className="pl-8" aria-label="Search markets" />
        </div>
      </div>
      <Card>
        {error && <div className="p-4 text-sm text-sell">{(error as Error).message}</div>}
        {!data && !error && <Skeleton className="m-4 h-64" />}
        {data && (
          <TableWrap>
            <table className="w-full min-w-[900px]">
              <thead className="border-b border-line">
                <tr>
                  <th className={clsx(th, "w-8")} aria-label="Watch" />
                  <th className={th}>Market</th>
                  <th className={clsx(th, "text-right")}>Last</th>
                  <th className={clsx(th, "text-right")}>Chg vs prior close</th>
                  <th className={th}>Data</th>
                  {tfs.map((t) => (
                    <th key={t} className={th}>
                      Analysis {t}
                    </th>
                  ))}
                  <th className={th}>Source</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((m) => (
                  <tr key={m.id} className="hover:bg-surface-2/50">
                    <td className={clsx(td, "pr-0")}>
                      <WatchStar assetId={m.id} watched={watched.has(m.id)} />
                    </td>
                    <td className={td}>
                      <Link href={`/charts?asset=${m.id}`} className="font-medium hover:text-accent">
                        {m.symbol}
                      </Link>
                      <div className="text-[11px] text-subtle">{m.name}</div>
                    </td>
                    <td className={clsx(td, "text-right")}>
                      <LivePriceCell m={m} />
                    </td>
                    <td className={clsx(td, "num text-right", (m.change_pct ?? 0) > 0 ? "text-buy" : (m.change_pct ?? 0) < 0 ? "text-sell" : "text-subtle")}>
                      {pct(m.change_pct)}
                    </td>
                    <td className={td} title={m.data_reason || undefined}>
                      <MarketStatus m={m} />
                      {m.data_reason && <div className="mt-1 max-w-[220px] truncate text-[10px] text-subtle">{m.data_reason}</div>}
                    </td>
                    {tfs.map((t) => {
                      const a = m.analysis[t];
                      return (
                        <td key={t} className={td}>
                          {a ? (
                            <Link href={`/signals/${a.id}`} className="inline-flex flex-col gap-1">
                              <StateBadge kind={signalKind(a)} size="sm" />
                              {a.status !== "DATA_UNAVAILABLE" && <RegimePill regime={a.regime} />}
                            </Link>
                          ) : (
                            <span className="text-xs text-subtle">not analysed yet</span>
                          )}
                        </td>
                      );
                    })}
                    <td className={clsx(td, "text-xs text-muted")}>
                      {m.provider}
                      {m.meta?.volume_note && <div className="text-[10px] text-subtle">{m.meta.volume_note}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
    </div>
  );
}
