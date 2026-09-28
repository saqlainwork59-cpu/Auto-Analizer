"use client";

import clsx from "clsx";
import { Star } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import useSWR, { mutate } from "swr";
import { api } from "@/lib/api";
import { pct, price } from "@/lib/format";
import { useLive } from "@/lib/providers";
import type { Market } from "@/lib/types";
import { StateBadge } from "./ui";

export function useMarkets() {
  return useSWR<{ markets: Market[]; analysis_timeframes: string[] }>("/api/markets", { refreshInterval: 30_000 });
}

export function useWatchlist() {
  return useSWR<{ asset_id: number; symbol: string; name: string; asset_class: string }[]>("/api/watchlist");
}

/** Live last price for a market: seeded from the REST snapshot, then updated from the WebSocket. */
export function useLivePrice(m: Market | undefined) {
  const { subscribe, on } = useLive();
  const [px, setPx] = useState<{ price: number; ts: string } | null>(m?.last ? { price: m.last.price, ts: m.last.ts } : null);
  useEffect(() => {
    setPx(m?.last ? { price: m.last.price, ts: m.last.ts } : null);
  }, [m?.last?.price, m?.last?.ts]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!m || m.data_status === "unavailable") return;
    const unsub = subscribe([m.id]);
    const off = on((msg) => {
      if (msg.asset_id !== m.id) return;
      if (msg.type === "price" && typeof msg.price === "number") setPx({ price: msg.price, ts: String(msg.ts) });
      if (msg.type === "candle" && msg.candle) setPx({ price: (msg.candle as { close: number }).close, ts: new Date().toISOString() });
    });
    return () => {
      off();
      unsub();
    };
  }, [m?.id, m?.data_status, subscribe, on]); // eslint-disable-line react-hooks/exhaustive-deps
  return px;
}

export function MarketStatus({ m }: { m: Market }) {
  if (m.data_status === "unavailable") return <StateBadge kind="DATA_UNAVAILABLE" size="sm" />;
  if (m.data_status === "stale") return <StateBadge kind="STALE" size="sm" />;
  return (
    <span className="inline-flex items-center gap-1 text-[11px] text-muted">
      <span className="h-1.5 w-1.5 rounded-full bg-buy" aria-hidden /> Live
    </span>
  );
}

export function MarketTile({ m }: { m: Market }) {
  const px = useLivePrice(m);
  return (
    <Link
      href={`/charts?asset=${m.id}`}
      className="min-w-[164px] flex-1 rounded-xl border border-line bg-surface px-3.5 py-3 transition-colors hover:border-line-strong"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs font-semibold">{m.symbol}</span>
        <span className="text-[10px] uppercase tracking-wider text-subtle">{m.asset_class}</span>
      </div>
      {m.data_status === "unavailable" || !px ? (
        <div className="mt-2">
          <div className="num text-base text-subtle">—</div>
          <div className="mt-1 truncate text-[11px] text-subtle" title={m.data_reason || undefined}>
            Data unavailable
          </div>
        </div>
      ) : (
        <div className="mt-2">
          <div className="num text-base font-semibold">{price(px.price, m.price_precision)}</div>
          <div className="mt-0.5 flex items-center justify-between">
            <span className={clsx("num text-[11px]", (m.change_pct ?? 0) > 0 ? "text-buy" : (m.change_pct ?? 0) < 0 ? "text-sell" : "text-muted")}>
              {pct(m.change_pct)}
            </span>
            {m.data_status === "stale" && <span className="text-[10px] text-wait">stale</span>}
          </div>
        </div>
      )}
    </Link>
  );
}

export function WatchStar({ assetId, watched }: { assetId: number; watched: boolean }) {
  const [busy, setBusy] = useState(false);
  async function toggle() {
    setBusy(true);
    try {
      if (watched) await api(`/api/watchlist/${assetId}`, { method: "DELETE" });
      else await api("/api/watchlist", { method: "POST", json: { asset_id: assetId } });
      await mutate("/api/watchlist");
    } finally {
      setBusy(false);
    }
  }
  return (
    <button
      onClick={toggle}
      disabled={busy}
      aria-pressed={watched}
      aria-label={watched ? "Remove from watchlist" : "Add to watchlist"}
      className="rounded-md p-1 text-subtle hover:bg-surface-2 hover:text-fg"
    >
      <Star className={clsx("h-4 w-4", watched && "fill-wait text-wait")} />
    </button>
  );
}

export function LivePriceCell({ m }: { m: Market }) {
  const px = useLivePrice(m);
  if (!px || m.data_status === "unavailable") return <span className="num text-subtle">—</span>;
  return <span className="num">{price(px.price, m.price_precision)}</span>;
}
