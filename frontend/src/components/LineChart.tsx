"use client";

import { AreaSeries, ColorType, CrosshairMode, LineStyle, createChart, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { useTheme } from "@/lib/providers";

function cssVar(n: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(n).trim();
}

/** Single-series time chart (equity curve, cumulative R). One series -> no legend box; the card title names it. */
export function TimeLineChart({
  points,
  height = 260,
  format,
  baseline,
}: {
  points: { time: string; value: number }[];
  height?: number;
  format?: (v: number) => string;
  baseline?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const { resolved } = useTheme();
  const [tip, setTip] = useState<{ x: number; y: number; t: string; v: number } | null>(null);
  useEffect(() => {
    if (!ref.current || points.length === 0) return;
    const accent = cssVar("--accent");
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: cssVar("--surface") }, textColor: cssVar("--fg-muted"), fontSize: 11, attributionLogo: true },
      grid: { vertLines: { visible: false }, horzLines: { color: cssVar("--line"), style: LineStyle.Dotted } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderColor: cssVar("--line"), timeVisible: true },
      crosshair: { mode: CrosshairMode.Magnet },
      handleScroll: false,
      handleScale: false,
    });
    const s = chart.addSeries(AreaSeries, {
      lineColor: accent,
      lineWidth: 2,
      topColor: accent + "33",
      bottomColor: accent + "00",
      priceLineVisible: false,
      priceFormat: format ? { type: "custom", formatter: format } : undefined,
    });
    // time must be strictly increasing: collapse duplicates (several exits in one second)
    const seen = new Map<number, number>();
    for (const p of points) seen.set(Math.floor(new Date(p.time).getTime() / 1000), p.value);
    const data = [...seen.entries()].sort((a, b) => a[0] - b[0]).map(([t, v]) => ({ time: t as UTCTimestamp, value: v }));
    s.setData(data);
    if (baseline !== undefined) s.createPriceLine({ price: baseline, color: cssVar("--fg-subtle"), lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false, title: "" });
    chart.timeScale().fitContent();
    chart.subscribeCrosshairMove((param) => {
      const d = param.seriesData.get(s) as { value: number } | undefined;
      if (!d || !param.point || param.time === undefined) return setTip(null);
      setTip({ x: param.point.x, y: param.point.y, t: new Date(Number(param.time) * 1000).toLocaleString(), v: d.value });
    });
    return () => chart.remove();
  }, [points, resolved, format, baseline]);
  return (
    <div className="relative w-full" style={{ height }}>
      <div ref={ref} className="h-full w-full" />
      {tip && (
        <div
          className="pointer-events-none absolute z-10 rounded-md border border-line bg-surface px-2 py-1 text-[11px] shadow-card"
          style={{ left: Math.min(tip.x + 12, 9999), top: Math.max(tip.y - 40, 0) }}
        >
          <div className="text-subtle">{tip.t}</div>
          <div className="num font-medium text-fg">{format ? format(tip.v) : tip.v.toFixed(2)}</div>
        </div>
      )}
    </div>
  );
}
