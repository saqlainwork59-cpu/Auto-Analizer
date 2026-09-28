export type Direction = "BUY" | "SELL" | "WAIT";
export type SignalStatus = "ACTIONABLE" | "WAIT" | "DATA_UNAVAILABLE";
export type TF = "5m" | "15m" | "1h" | "4h" | "1d";
export const TIMEFRAMES: TF[] = ["5m", "15m", "1h", "4h", "1d"];

export interface User {
  id: number;
  email: string;
  display_name: string | null;
  role: "user" | "admin";
  settings: { theme?: "light" | "dark" | "system"; default_timeframe?: TF };
}

export interface AssetRef {
  id: number;
  symbol: string;
  name: string;
  asset_class: string;
  price_precision: number;
}

export interface Outcome {
  status: string;
  filled_at: string | null;
  fill_price: number | null;
  exit_at: string | null;
  exit_price: number | null;
  r_multiple: number | null;
  mfe_r: number | null;
  mae_r: number | null;
  detail: { legs?: { fraction: number; price: number; reason: string }[]; note?: string; basis?: string };
}

export interface Component {
  name: string;
  weight: number;
  score: number;
  points: number | null;
  available: boolean;
  evidence: string[];
  against: string[];
}

export interface MtfTf {
  available: boolean;
  bias: string;
  detail?: string;
  score: number | null;
  regime: string | null;
}

export interface Signal {
  id: number;
  created_at: string;
  bar_time: string;
  asset: AssetRef;
  timeframe: TF;
  status: SignalStatus;
  direction: Direction;
  setup_type: string | null;
  regime: string;
  score: number;
  entry_low: number | null;
  entry_high: number | null;
  entry_ref: number | null;
  stop_loss: number | null;
  tp1: number | null;
  tp2: number | null;
  rr_tp1: number | null;
  rr_tp2: number | null;
  expires_at: string | null;
  headline: string;
  strategy_version: string;
  engine_version: string;
  model_version_id: number | null;
  ml_probability: number | null;
  outcome: Outcome | null;
  explanation?: string;
  reasons?: string[];
  risks?: string[];
  invalidation?: string[];
  components?: Component[];
  mtf?: {
    timeframes?: Record<string, MtfTf>;
    agreement?: number | null;
    coverage?: number;
    opposition?: number | null;
    conflict?: boolean;
    supporting?: string[];
    opposing?: string[];
    neutral?: string[];
    missing?: string[];
  };
  levels_meta?: {
    methods?: Record<string, string>;
    risk_atr?: number | null;
    zones?: { low: number; high: number; touches: number; kind: string }[];
    min_score?: number;
    min_rr?: number;
    sim?: Record<string, number | boolean>;
  };
  data_quality?: { ok?: boolean; issues?: string[]; warnings?: string[]; bars?: number; last_close?: string };
  features?: Record<string, number | null>;
}

export interface LastPrice {
  price: number;
  ts: string;
  source: string;
  age_seconds: number;
  stale: boolean;
}

export interface AnalysisSummary {
  id: number;
  status: SignalStatus;
  direction: Direction;
  score: number;
  regime: string;
  bar_time: string;
  headline: string;
}

export interface Market {
  id: number;
  symbol: string;
  name: string;
  asset_class: string;
  provider: string;
  calendar: string;
  price_precision: number;
  meta: Record<string, string>;
  data_status: "ok" | "stale" | "unavailable";
  data_reason: string | null;
  last: LastPrice | null;
  change_pct: number | null;
  analysis: Record<string, AnalysisSummary | null>;
}

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
}

export interface CandleResponse {
  asset: AssetRef & { provider: string; calendar: string };
  timeframe: TF;
  candles: Candle[];
  forming: Candle | null;
  overlays: Record<string, { time: number; value: number }[]>;
  signals?: Signal[];
  data_status: string;
  data_reason: string | null;
  stale?: boolean;
  last_closed_at?: string;
}

export interface PaperTrade {
  id: number;
  asset: { id: number; symbol: string; price_precision: number };
  signal_id: number | null;
  direction: "BUY" | "SELL";
  quantity: number;
  entry_price: number;
  stop_loss: number | null;
  take_profit: number | null;
  status: "OPEN" | "CLOSED";
  opened_at: string;
  closed_at: string | null;
  exit_price: number | null;
  close_reason: string | null;
  pnl: number | null;
  fees: number;
  price_source: string;
  current_price: number | null;
  unrealized_pnl: number | null;
}
