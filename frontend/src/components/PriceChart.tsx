"use client";

import clsx from "clsx";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { Ban } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { price as fmtPrice } from "@/lib/format";
import { useLive, useTheme } from "@/lib/providers";
import type { Candle, CandleResponse, Signal, TF } from "@/lib/types";
import { Skeleton, StateBadge } from "./ui";

const TF_SECONDS: Record<TF, number> = { "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400 };

export interface Indicators {
  ema: boolean;
  bb: boolean;
  vwap: boolean;
  volume: boolean;
  rsi: boolean;
  macd: boolean;
  zones: boolean;
}

export const DEFAULT_INDICATORS: Indicators = { ema: true, bb: false, vwap: false, volume: true, rsi: true, macd: false, zones: true };

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

function palette() {
  return {
    surface: cssVar("--surface"),
    text: cssVar("--fg-muted"),
    subtle: cssVar("--fg-subtle"),
    line: cssVar("--line"),
    up: cssVar("--chart-up"),
    down: cssVar("--chart-down"),
    accent: cssVar("--accent"),
    buy: cssVar("--buy"),
    sell: cssVar("--sell"),
    s1: cssVar("--series-1"),
    s2: cssVar("--series-2"),
    s3: cssVar("--series-3"),
    s4: cssVar("--series-4"),
  };
}

const ts = (t: number) => t as UTCTimestamp;

interface Props {
  assetId: number;
  tf: TF;
  height?: number;
  indicators?: Partial<Indicators>;
  signal?: Signal | null; // draws entry / stop / targets
  limit?: number;
  compact?: boolean;
  /** Show history around a past moment (epoch seconds of the last candle) instead of the live edge. */
  until?: number;
}

export function PriceChart({ assetId, tf, height = 460, indicators, signal, limit = 400, compact = false, until }: Props) {
  const ind = { ...DEFAULT_INDICATORS, ...(indicators || {}) };
  const { data, error, isLoading } = useSWR<CandleResponse>(`/api/markets/${assetId}/candles?tf=${tf}&limit=${limit}${until ? `&until=${until}` : ""}`, {
    refreshInterval: until ? 0 : 60_000,
  });
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const series = useRef<{
    candle?: ISeriesApi<"Candlestick">;
    volume?: ISeriesApi<"Histogram">;
    last?: Candle;
    lines: IPriceLine[];
    markers?: ISeriesMarkersPluginApi<Time>;
  }>({ lines: [] });
  const { resolved } = useTheme();
  const { subscribe, on } = useLive();
  const [hover, setHover] = useState<Candle | null>(null);
  const precision = data?.asset.price_precision ?? 2;

  const legend = useMemo(() => {
    const items: { label: string; color: string }[] = [];
    if (ind.ema) items.push({ label: "EMA 20", color: "var(--series-1)" }, { label: "EMA 50", color: "var(--series-2)" }, { label: "EMA 200", color: "var(--series-3)" });
    if (ind.bb) items.push({ label: "Bollinger 20,2", color: "var(--fg-subtle)" });
    if (ind.vwap) items.push({ label: "VWAP", color: "var(--series-4)" });
    return items;
  }, [ind.ema, ind.bb, ind.vwap]);

  // build / rebuild the chart when data, theme or indicator set changes
  useEffect(() => {
    if (!box.current || !data || data.candles.length === 0) return;
    const p = palette();
    const chart = createChart(box.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: p.surface },
        textColor: p.text,
        fontFamily: "Inter Variable, ui-sans-serif, system-ui, sans-serif",
        fontSize: 11,
        attributionLogo: true,
        panes: { separatorColor: p.line, separatorHoverColor: p.line, enableResize: true },
      },
      grid: { vertLines: { color: p.line, style: LineStyle.Dotted }, horzLines: { color: p.line, style: LineStyle.Dotted } },
      rightPriceScale: { borderColor: p.line },
      timeScale: { borderColor: p.line, timeVisible: tf !== "1d", secondsVisible: false, rightOffset: 6 },
      crosshair: { mode: CrosshairMode.Normal },
    });
    chartRef.current = chart;
    const minMove = 1 / 10 ** precision;
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: p.up,
      downColor: p.down,
      wickUpColor: p.up,
      wickDownColor: p.down,
      borderVisible: false,
      priceFormat: { type: "price", precision, minMove },
    });
    const bars = data.candles.map((c) => ({ time: ts(c.time), open: c.open, high: c.high, low: c.low, close: c.close }));
    const forming = data.forming && data.forming.time > data.candles[data.candles.length - 1].time ? data.forming : null;
    candle.setData(forming ? [...bars, { ...forming, time: ts(forming.time) }] : bars);
    series.current = { candle, lines: [], last: forming || data.candles[data.candles.length - 1] };

    const hasVol = data.candles.some((c) => c.volume != null && c.volume > 0);
    if (ind.volume && hasVol) {
      const vol = chart.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
      chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      vol.setData(
        data.candles.map((c) => ({ time: ts(c.time), value: c.volume || 0, color: (c.close >= c.open ? p.up : p.down) + "55" })),
      );
      series.current.volume = vol;
    }
    const line = (key: string, color: string, width: 1 | 2 = 2, style: LineStyle = LineStyle.Solid, pane = 0) => {
      const pts = data.overlays[key];
      if (!pts || pts.length === 0) return;
      const s = chart.addSeries(
        LineSeries,
        { color, lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false },
        pane,
      );
      s.setData(pts.map((x) => ({ time: ts(x.time), value: x.value })));
      return s;
    };
    if (ind.ema) {
      line("ema20", p.s1);
      line("ema50", p.s2);
      line("ema200", p.s3);
    }
    if (ind.bb) {
      line("bb_up", p.subtle, 1, LineStyle.Dashed);
      line("bb_mid", p.subtle, 1, LineStyle.Dotted);
      line("bb_low", p.subtle, 1, LineStyle.Dashed);
    }
    if (ind.vwap) line("vwap", p.s4, 2);
    let pane = 1;
    if (ind.rsi && data.overlays.rsi14?.length) {
      const rs = line("rsi14", p.s1, 2, LineStyle.Solid, pane);
      rs?.createPriceLine({ price: 70, color: p.subtle, lineStyle: LineStyle.Dotted, lineWidth: 1, axisLabelVisible: false, title: "" });
      rs?.createPriceLine({ price: 30, color: p.subtle, lineStyle: LineStyle.Dotted, lineWidth: 1, axisLabelVisible: false, title: "" });
      pane += 1;
    }
    if (ind.macd && data.overlays.macd_hist?.length) {
      const h = chart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, pane);
      h.setData(data.overlays.macd_hist.map((x) => ({ time: ts(x.time), value: x.value, color: (x.value >= 0 ? p.up : p.down) + "99" })));
      line("macd", p.s1, 1, LineStyle.Solid, pane);
      line("macd_signal", p.s2, 1, LineStyle.Solid, pane);
      pane += 1;
    }
    const panes = chart.panes();
    if (panes.length > 1) {
      panes[0].setStretchFactor(3.2);
      for (let i = 1; i < panes.length; i++) panes[i].setStretchFactor(1);
    }

    // historical signal markers (all actionable signals in view, winners and losers)
    const markers = (data.signals || [])
      .filter((s) => s.status === "ACTIONABLE")
      .map((s) => {
        const barOpen = Math.floor(new Date(s.bar_time).getTime() / 1000) - TF_SECONDS[tf];
        const buy = s.direction === "BUY";
        return {
          time: ts(barOpen),
          position: buy ? ("belowBar" as const) : ("aboveBar" as const),
          shape: buy ? ("arrowUp" as const) : ("arrowDown" as const),
          color: buy ? p.buy : p.sell,
          text: `${s.direction} ${s.score.toFixed(0)}`,
        };
      });
    series.current.markers = createSeriesMarkers(candle, markers);

    chart.subscribeCrosshairMove((param) => {
      const d = param.seriesData.get(candle) as { open: number; high: number; low: number; close: number } | undefined;
      if (!d || param.time === undefined) return setHover(null);
      setHover({ time: Number(param.time), open: d.open, high: d.high, low: d.low, close: d.close, volume: null });
    });
    chart.timeScale().fitContent();
    if (bars.length > 160) chart.timeScale().setVisibleLogicalRange({ from: bars.length - 160, to: bars.length + 5 });
    return () => {
      chart.remove();
      chartRef.current = null;
      series.current = { lines: [] };
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, resolved, ind.ema, ind.bb, ind.vwap, ind.volume, ind.rsi, ind.macd, tf, precision]);

  // levels of the selected signal + S/R zones
  useEffect(() => {
    const c = series.current.candle;
    if (!c) return;
    series.current.lines.forEach((l) => c.removePriceLine(l));
    series.current.lines = [];
    const p = palette();
    const add = (price: number | null | undefined, color: string, title: string, style = LineStyle.Solid, width: 1 | 2 = 1) => {
      if (price == null) return;
      series.current.lines.push(c.createPriceLine({ price, color, lineWidth: width, lineStyle: style, axisLabelVisible: true, title }));
    };
    if (signal && signal.status === "ACTIONABLE") {
      add(signal.entry_high, p.accent, "Entry", LineStyle.Dashed);
      add(signal.entry_low, p.accent, "", LineStyle.Dashed);
      add(signal.stop_loss, p.sell, "Stop", LineStyle.Solid, 2);
      add(signal.tp1, p.buy, "TP1", LineStyle.Solid, 2);
      add(signal.tp2, p.buy, "TP2", LineStyle.Dashed);
    }
    if (ind.zones && signal?.levels_meta?.zones) {
      for (const z of signal.levels_meta.zones) {
        add((z.low + z.high) / 2, p.subtle, `${z.kind === "support" ? "S" : "R"} ×${z.touches}`, LineStyle.Dotted);
      }
    }
  }, [signal, ind.zones, data, resolved]);

  // live updates
  useEffect(() => {
    if (until) return;
    const unsub = subscribe([assetId]);
    const off = on((m) => {
      if (m.asset_id !== assetId) return;
      const s = series.current;
      if (!s.candle || !s.last) return;
      if (m.type === "candle" && m.timeframe === tf && m.candle) {
        const c = m.candle as Candle;
        if (c.time < s.last.time) return;
        s.candle.update({ time: ts(c.time), open: c.open, high: c.high, low: c.low, close: c.close });
        if (s.volume && c.volume != null) {
          const p = palette();
          s.volume.update({ time: ts(c.time), value: c.volume, color: (c.close >= c.open ? p.up : p.down) + "55" });
        }
        s.last = c;
      } else if (m.type === "price" && typeof m.price === "number") {
        // tick feeds (FX / equities): extend the forming candle from verified ticks
        const t = Math.floor(new Date(String(m.ts)).getTime() / 1000);
        const bucket = t - (t % TF_SECONDS[tf]);
        const px = m.price;
        const next: Candle =
          bucket > s.last.time
            ? { time: bucket, open: px, high: px, low: px, close: px, volume: null }
            : { ...s.last, high: Math.max(s.last.high, px), low: Math.min(s.last.low, px), close: px };
        if (bucket < s.last.time) return;
        s.candle.update({ time: ts(next.time), open: next.open, high: next.high, low: next.low, close: next.close });
        s.last = next;
      }
    });
    return () => {
      off();
      unsub();
    };
  }, [assetId, tf, subscribe, on, until]);

  const unavailable = data && data.candles.length === 0;
  const last = hover || (data && data.candles.length ? series.current.last || data.candles[data.candles.length - 1] : null);

  return (
    <div className="relative w-full" style={{ height }}>
      {!compact && data && data.candles.length > 0 && (
        <div className="pointer-events-none absolute left-2 top-2 z-10 flex max-w-[calc(100%-96px)] flex-wrap items-center gap-x-3 gap-y-1 rounded-md bg-surface/85 px-2 py-1 text-[11px] backdrop-blur">
          <span className="font-semibold text-fg">
            {data.asset.symbol} · {tf}
          </span>
          {last && (
            <span className="num text-muted">
              O {fmtPrice(last.open, precision)} H {fmtPrice(last.high, precision)} L {fmtPrice(last.low, precision)} C{" "}
              <span className={last.close >= last.open ? "text-buy" : "text-sell"}>{fmtPrice(last.close, precision)}</span>
            </span>
          )}
          {legend.map((l) => (
            <span key={l.label} className="inline-flex items-center gap-1 text-muted">
              <span className="inline-block h-0.5 w-3 rounded" style={{ background: l.color }} aria-hidden />
              {l.label}
            </span>
          ))}
          {data.stale && <StateBadge kind="STALE" size="sm" />}
        </div>
      )}
      <div ref={box} className={clsx("h-full w-full", (unavailable || isLoading || error) && "invisible")} />
      {isLoading && <Skeleton className="absolute inset-0" />}
      {(unavailable || error) && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 rounded-lg bg-surface-2/60 px-6 text-center">
          <Ban className="h-5 w-5 text-na" aria-hidden />
          <StateBadge kind="DATA_UNAVAILABLE" />
          <p className="max-w-sm text-xs leading-relaxed text-muted">
            {error ? (error as Error).message : data?.data_reason || "No stored candles for this market and timeframe yet."}
          </p>
        </div>
      )}
    </div>
  );
}
