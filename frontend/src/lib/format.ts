export function price(v: number | null | undefined, precision = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const p = Math.abs(v) >= 1000 ? Math.min(precision, 2) : precision;
  return v.toLocaleString("en-US", { minimumFractionDigits: p, maximumFractionDigits: p });
}

export function money(v: number | null | undefined, signed = false): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const s = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (!signed) return (v < 0 ? "-$" : "$") + s;
  return (v > 0 ? "+$" : v < 0 ? "-$" : "$") + s;
}

export function pct(v: number | null | undefined, digits = 2, signed = true): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${signed && v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

export function ratio(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `1:${v.toFixed(2)}`;
}

export function r(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}R`;
}

export function num(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || (typeof v === "number" && !Number.isFinite(v))) return "—";
  return Number(v).toLocaleString("en-US", { maximumFractionDigits: digits });
}

export function dateTime(v: string | null | undefined): string {
  if (!v) return "—";
  const d = new Date(v);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function ago(v: string | null | undefined): string {
  if (!v) return "—";
  const s = (Date.now() - new Date(v).getTime()) / 1000;
  if (s < 0) return "in " + ago(new Date(Date.now() - s * 1000 * 2).toISOString()).replace(" ago", "");
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export function regimeLabel(r: string | null | undefined): string {
  return (r || "UNCERTAIN").replace(/_/g, " ");
}

export function setupLabel(s: string | null | undefined): string {
  return s ? s.replace(/_/g, " ") : "—";
}

export const COMPONENT_LABEL: Record<string, string> = {
  trend_alignment: "Trend / regime alignment",
  momentum: "Momentum",
  market_structure: "Market structure",
  volume: "Volume confirmation",
  support_resistance: "Support / resistance",
  multi_timeframe: "Multi-timeframe",
  risk_reward: "Risk / reward",
};
