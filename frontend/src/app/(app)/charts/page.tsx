"use client";

import clsx from "clsx";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import useSWR, { mutate } from "swr";
import { useMarkets } from "@/components/MarketBits";
import { DEFAULT_INDICATORS, type Indicators, PriceChart } from "@/components/PriceChart";
import { Button, Card, CardHeader, ErrorNote, RegimePill, ScoreRing, Segmented, Select, Skeleton, StateBadge, signalKind } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime, price, ratio, setupLabel } from "@/lib/format";
import { useAuth } from "@/lib/providers";
import type { Signal, TF } from "@/lib/types";

const TOGGLES: { key: keyof Indicators; label: string }[] = [
  { key: "ema", label: "EMA 20/50/200" },
  { key: "bb", label: "Bollinger" },
  { key: "vwap", label: "VWAP" },
  { key: "volume", label: "Volume" },
  { key: "rsi", label: "RSI" },
  { key: "macd", label: "MACD" },
  { key: "zones", label: "S/R zones" },
];

function ChartsInner() {
  const params = useSearchParams();
  const router = useRouter();
  const { user } = useAuth();
  const { data: mk } = useMarkets();
  const assetId = Number(params.get("asset")) || mk?.markets[0]?.id || 0;
  const [tf, setTf] = useState<TF>((user?.settings.default_timeframe as TF) || "1h");
  const [ind, setInd] = useState<Indicators>(DEFAULT_INDICATORS);
  useEffect(() => {
    try {
      const saved = localStorage.getItem("px-indicators");
      if (saved) setInd({ ...DEFAULT_INDICATORS, ...JSON.parse(saved) });
    } catch {}
  }, []);
  const toggle = (k: keyof Indicators) => {
    const next = { ...ind, [k]: !ind[k] };
    setInd(next);
    try {
      localStorage.setItem("px-indicators", JSON.stringify(next));
    } catch {}
  };
  const key = assetId ? `/api/markets/${assetId}/analysis?tf=${tf}` : null;
  const { data: latest } = useSWR<{ signal: Signal | null }>(key, { refreshInterval: 30_000 });
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const market = mk?.markets.find((m) => m.id === assetId);
  const s = latest?.signal;

  async function runNow() {
    setRunning(true);
    setErr(null);
    try {
      await api(`/api/markets/${assetId}/analyze?tf=${tf}`, { method: "POST" });
      await mutate(key);
    } catch (e) {
      setErr(e);
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
      <Card className="min-w-0">
        <div className="flex flex-wrap items-center gap-2 border-b border-line p-3">
          <Select
            value={assetId || ""}
            onChange={(e) => router.replace(`/charts?asset=${e.target.value}`)}
            className="h-8 w-40 text-xs"
            aria-label="Market"
          >
            {mk?.markets.map((m) => (
              <option key={m.id} value={m.id}>
                {m.symbol} — {m.name}
              </option>
            ))}
          </Select>
          <Segmented size="sm" value={tf} onChange={setTf} options={(["5m", "15m", "1h", "4h", "1d"] as TF[]).map((t) => ({ value: t, label: t }))} />
          <div className="flex flex-wrap gap-1">
            {TOGGLES.map((t) => (
              <button
                key={t.key}
                onClick={() => toggle(t.key)}
                aria-pressed={ind[t.key]}
                className={clsx(
                  "rounded-md border px-2 py-1 text-[11px] font-medium",
                  ind[t.key] ? "border-accent/40 bg-accent-soft text-accent" : "border-line text-muted hover:text-fg",
                )}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>
        <div className="p-2">{assetId ? <PriceChart assetId={assetId} tf={tf} indicators={ind} signal={s} height={640} limit={600} /> : <Skeleton className="h-[640px]" />}</div>
      </Card>

      <div className="space-y-5">
        <Card>
          <CardHeader
            title={`Latest analysis · ${tf}`}
            subtitle={s ? `Candle closed ${dateTime(s.bar_time)}` : "No evaluation stored yet"}
            action={
              <Button size="sm" onClick={runNow} loading={running} disabled={!assetId}>
                Analyse now
              </Button>
            }
          />
          <div className="space-y-3 p-4">
            {running && <StateBadge kind="ANALYZING" />}
            <ErrorNote error={err} />
            {!s && !running && <p className="text-sm text-muted">Analysis runs automatically when each candle closes. You can also trigger it for the latest closed candle.</p>}
            {s && (
              <>
                <div className="flex items-center gap-3">
                  <ScoreRing score={s.score} threshold={s.levels_meta?.min_score ?? 70} size={64} />
                  <div className="min-w-0 space-y-1.5">
                    <StateBadge kind={signalKind(s)} />
                    <div>
                      <RegimePill regime={s.regime} />
                    </div>
                  </div>
                </div>
                <p className="text-sm font-medium">{s.headline}</p>
                <p className="text-xs leading-relaxed text-muted">{s.explanation}</p>
                {s.status === "ACTIONABLE" && market && (
                  <dl className="grid grid-cols-2 gap-2 text-xs">
                    <dt className="text-subtle">Setup</dt>
                    <dd className="capitalize">{setupLabel(s.setup_type)}</dd>
                    <dt className="text-subtle">Entry</dt>
                    <dd className="num">
                      {price(s.entry_low, market.price_precision)} – {price(s.entry_high, market.price_precision)}
                    </dd>
                    <dt className="text-subtle">Stop</dt>
                    <dd className="num text-sell">{price(s.stop_loss, market.price_precision)}</dd>
                    <dt className="text-subtle">TP1 / TP2</dt>
                    <dd className="num text-buy">
                      {price(s.tp1, market.price_precision)} / {price(s.tp2, market.price_precision)}
                    </dd>
                    <dt className="text-subtle">R:R</dt>
                    <dd className="num">{ratio(s.rr_tp1)}</dd>
                  </dl>
                )}
                <Link href={`/signals/${s.id}`} className="inline-block text-xs font-medium text-accent hover:underline">
                  Full analysis, score breakdown & invalidation →
                </Link>
              </>
            )}
          </div>
        </Card>
        {market && (
          <Card>
            <CardHeader title="Market" />
            <dl className="grid grid-cols-2 gap-y-2 p-4 text-xs">
              <dt className="text-subtle">Provider</dt>
              <dd>{market.provider}</dd>
              <dt className="text-subtle">Session</dt>
              <dd>{market.calendar === "24x7" ? "24/7" : market.calendar === "fx" ? "FX (24/5)" : "Exchange hours"}</dd>
              <dt className="text-subtle">Data</dt>
              <dd className={market.data_status === "ok" ? "text-buy" : "text-wait"}>{market.data_status}</dd>
              {market.data_reason && <dd className="col-span-2 text-subtle">{market.data_reason}</dd>}
              {market.meta?.volume_note && <dd className="col-span-2 text-subtle">{market.meta.volume_note}</dd>}
            </dl>
          </Card>
        )}
      </div>
    </div>
  );
}

export default function ChartsPage() {
  return (
    <Suspense fallback={<Skeleton className="h-[640px]" />}>
      <ChartsInner />
    </Suspense>
  );
}
