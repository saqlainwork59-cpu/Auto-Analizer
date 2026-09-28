"use client";

import { Plus, Star, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { mutate } from "swr";
import { LivePriceCell, MarketStatus, useMarkets, useWatchlist } from "@/components/MarketBits";
import { PriceChart } from "@/components/PriceChart";
import { Button, Card, Empty, PageHeader, RegimePill, ScoreBar, Select, Skeleton, StateBadge, signalKind } from "@/components/ui";
import { api } from "@/lib/api";
import { pct } from "@/lib/format";

export default function WatchlistPage() {
  const { data: mk } = useMarkets();
  const { data: wl } = useWatchlist();
  const [add, setAdd] = useState("");
  const watched = new Set((wl || []).map((w) => w.asset_id));
  const items = (wl || []).map((w) => mk?.markets.find((m) => m.id === w.asset_id)).filter(Boolean) as NonNullable<typeof mk>["markets"];
  const tfs = mk?.analysis_timeframes || [];

  async function addAsset() {
    if (!add) return;
    await api("/api/watchlist", { method: "POST", json: { asset_id: Number(add) } });
    setAdd("");
    mutate("/api/watchlist");
  }
  async function remove(id: number) {
    await api(`/api/watchlist/${id}`, { method: "DELETE" });
    mutate("/api/watchlist");
  }

  return (
    <div>
      <PageHeader
        title="Watchlist"
        subtitle="Markets you follow. Alerts with no explicit market list use this watchlist."
        actions={
          <div className="flex gap-2">
            <Select value={add} onChange={(e) => setAdd(e.target.value)} className="h-9 w-48" aria-label="Market to add">
              <option value="">Add a market…</option>
              {mk?.markets
                .filter((m) => !watched.has(m.id))
                .map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.symbol}
                  </option>
                ))}
            </Select>
            <Button variant="primary" onClick={addAsset} disabled={!add}>
              <Plus className="h-4 w-4" /> Add
            </Button>
          </div>
        }
      />
      {!wl && <Skeleton className="h-64" />}
      {wl && items.length === 0 && (
        <Card>
          <Empty icon={<Star className="h-6 w-6" />} title="Your watchlist is empty" body="Add markets to see them on the dashboard and receive watchlist alerts." />
        </Card>
      )}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 2xl:grid-cols-3">
        {items.map((m) => (
          <Card key={m.id} className="overflow-hidden">
            <div className="flex items-start justify-between gap-3 px-4 pt-4">
              <div>
                <Link href={`/charts?asset=${m.id}`} className="text-base font-semibold hover:text-accent">
                  {m.symbol}
                </Link>
                <div className="text-xs text-muted">{m.name}</div>
              </div>
              <div className="text-right">
                <div className="text-base">
                  <LivePriceCell m={m} />
                </div>
                <div className={(m.change_pct ?? 0) >= 0 ? "num text-xs text-buy" : "num text-xs text-sell"}>{pct(m.change_pct)}</div>
              </div>
            </div>
            <div className="px-2 pt-2">
              <PriceChart assetId={m.id} tf="1h" height={150} compact limit={120} indicators={{ volume: false, rsi: false, macd: false, ema: true, zones: false }} />
            </div>
            <div className="divide-y divide-line border-t border-line">
              {tfs.map((t) => {
                const a = m.analysis[t];
                return (
                  <Link key={t} href={a ? `/signals/${a.id}` : `/charts?asset=${m.id}`} className="flex items-center gap-3 px-4 py-2 text-xs hover:bg-surface-2/50">
                    <span className="w-8 font-medium text-muted">{t}</span>
                    {a ? <StateBadge kind={signalKind(a)} size="sm" /> : <MarketStatus m={m} />}
                    {a && a.status !== "DATA_UNAVAILABLE" && <RegimePill regime={a.regime} />}
                    <span className="ml-auto">{a && a.score > 0 && <ScoreBar score={a.score} />}</span>
                  </Link>
                );
              })}
            </div>
            <div className="flex justify-end border-t border-line px-3 py-2">
              <Button size="sm" variant="ghost" onClick={() => remove(m.id)}>
                <Trash2 className="h-3.5 w-3.5" /> Remove
              </Button>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
