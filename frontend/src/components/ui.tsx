"use client";

import clsx from "clsx";
import { AlertTriangle, Ban, CircleDashed, Loader2, MinusCircle, TrendingDown, TrendingUp } from "lucide-react";
import Link from "next/link";
import { forwardRef } from "react";

export function Card({ className, children, ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={clsx("min-w-0 rounded-xl border border-line bg-surface shadow-card", className)} {...rest}>
      {children}
    </div>
  );
}

export function CardHeader({ title, subtitle, action, className }: { title: React.ReactNode; subtitle?: React.ReactNode; action?: React.ReactNode; className?: string }) {
  return (
    <div className={clsx("flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3", className)}>
      <div className="min-w-0">
        <h2 className="text-[13px] font-semibold tracking-tight text-fg">{title}</h2>
        {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

type BtnProps = React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger" | "buy" | "sell"; size?: "sm" | "md"; loading?: boolean };

export const Button = forwardRef<HTMLButtonElement, BtnProps>(function Button(
  { variant = "secondary", size = "md", loading, className, children, disabled, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-8 px-2.5 text-xs" : "h-9 px-3.5 text-sm",
        variant === "primary" && "bg-accent text-accent-fg hover:opacity-90",
        variant === "secondary" && "border border-line bg-surface text-fg hover:bg-surface-2",
        variant === "ghost" && "text-muted hover:bg-surface-2 hover:text-fg",
        variant === "danger" && "bg-sell text-white hover:opacity-90",
        variant === "buy" && "bg-buy text-white hover:opacity-90",
        variant === "sell" && "bg-sell text-white hover:opacity-90",
        className,
      )}
      {...rest}
    >
      {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
      {children}
    </button>
  );
});

export const Input = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...rest }, ref) {
  return (
    <input
      ref={ref}
      className={clsx(
        "h-9 rounded-lg border border-line bg-surface px-3 text-sm text-fg placeholder:text-subtle focus:border-accent focus:outline-none",
        !/(^|\s)w-/.test(className || "") && "w-full",
        className,
      )}
      {...rest}
    />
  );
});

export function Select({ className, children, ...rest }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={clsx(
        "h-9 rounded-lg border border-line bg-surface px-2.5 text-sm text-fg focus:border-accent focus:outline-none",
        !/(^|\s)w-/.test(className || "") && "w-full",
        className,
      )}
      {...rest}
    >
      {children}
    </select>
  );
}

export function Field({ label, hint, children, className }: { label: string; hint?: string; children: React.ReactNode; className?: string }) {
  return (
    <label className={clsx("block", className)}>
      <span className="mb-1 block text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-subtle">{hint}</span>}
    </label>
  );
}

export function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={clsx(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-50",
        checked ? "bg-accent" : "bg-surface-3",
      )}
    >
      <span className={clsx("inline-block h-4 w-4 rounded-full bg-white shadow transition-transform", checked ? "translate-x-4.5" : "translate-x-0.5")} />
    </button>
  );
}

export function Segmented<T extends string>({ value, options, onChange, size = "md" }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; size?: "sm" | "md" }) {
  return (
    <div className="inline-flex rounded-lg border border-line bg-surface-2 p-0.5" role="tablist">
      {options.map((o) => (
        <button
          key={o.value}
          role="tab"
          aria-selected={o.value === value}
          onClick={() => onChange(o.value)}
          className={clsx(
            "rounded-md font-medium transition-colors",
            size === "sm" ? "px-2 py-1 text-[11px]" : "px-3 py-1.5 text-xs",
            o.value === value ? "bg-surface text-fg shadow-card" : "text-muted hover:text-fg",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- signal state labels (icon + text, never color alone) */
export type StateKind = "BUY" | "SELL" | "WAIT" | "DATA_UNAVAILABLE" | "ANALYZING" | "STALE";

export function StateBadge({ kind, size = "md", className }: { kind: StateKind; size?: "sm" | "md"; className?: string }) {
  const map = {
    BUY: { cls: "bg-buy-soft text-buy", Icon: TrendingUp, text: "BUY" },
    SELL: { cls: "bg-sell-soft text-sell", Icon: TrendingDown, text: "SELL" },
    WAIT: { cls: "bg-wait-soft text-wait", Icon: MinusCircle, text: "WAIT" },
    DATA_UNAVAILABLE: { cls: "bg-na-soft text-na", Icon: Ban, text: "DATA UNAVAILABLE" },
    ANALYZING: { cls: "bg-accent-soft text-accent", Icon: CircleDashed, text: "ANALYSIS IN PROGRESS" },
    STALE: { cls: "bg-wait-soft text-wait", Icon: AlertTriangle, text: "STALE DATA" },
  }[kind];
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-md font-semibold tracking-wide",
        size === "sm" ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-1 text-[11px]",
        map.cls,
        className,
      )}
    >
      <map.Icon className={clsx(size === "sm" ? "h-3 w-3" : "h-3.5 w-3.5", kind === "ANALYZING" && "animate-spin")} aria-hidden />
      {map.text}
    </span>
  );
}

export function signalKind(s: { status: string; direction: string } | null | undefined): StateKind {
  if (!s) return "DATA_UNAVAILABLE";
  if (s.status === "DATA_UNAVAILABLE") return "DATA_UNAVAILABLE";
  if (s.status === "ACTIONABLE") return s.direction === "SELL" ? "SELL" : "BUY";
  return "WAIT";
}

export function Pill({ children, tone = "neutral", className }: { children: React.ReactNode; tone?: "neutral" | "buy" | "sell" | "wait" | "accent"; className?: string }) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium",
        tone === "neutral" && "bg-surface-2 text-muted",
        tone === "buy" && "bg-buy-soft text-buy",
        tone === "sell" && "bg-sell-soft text-sell",
        tone === "wait" && "bg-wait-soft text-wait",
        tone === "accent" && "bg-accent-soft text-accent",
        className,
      )}
    >
      {children}
    </span>
  );
}

export function RegimePill({ regime }: { regime: string | null | undefined }) {
  const r = regime || "UNCERTAIN";
  const tone = r === "TRENDING_UP" ? "buy" : r === "TRENDING_DOWN" ? "sell" : r === "BREAKOUT" ? "accent" : r === "HIGH_VOLATILITY" ? "wait" : "neutral";
  return <Pill tone={tone}>{r.replace(/_/g, " ")}</Pill>;
}

export function ScoreBar({ score, threshold = 70 }: { score: number; threshold?: number }) {
  const s = Math.max(0, Math.min(100, score));
  return (
    <div className="flex items-center gap-2">
      <div className="relative h-1.5 w-20 overflow-hidden rounded-full bg-surface-3" aria-hidden>
        <div className={clsx("h-full rounded-full", s >= threshold ? "bg-accent" : "bg-subtle")} style={{ width: `${s}%` }} />
        <div className="absolute top-0 h-full w-px bg-fg/40" style={{ left: `${threshold}%` }} />
      </div>
      <span className="num text-xs text-fg">{score ? score.toFixed(0) : "—"}</span>
    </div>
  );
}

export function ScoreRing({ score, threshold = 70, size = 76 }: { score: number; threshold?: number; size?: number }) {
  const s = Math.max(0, Math.min(100, score || 0));
  const r = size / 2 - 5;
  const c = 2 * Math.PI * r;
  return (
    <div className="relative" style={{ width: size, height: size }} role="img" aria-label={`Setup score ${s.toFixed(0)} of 100`}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--surface-3)" strokeWidth={6} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={s >= threshold ? "var(--accent)" : "var(--fg-subtle)"}
          strokeWidth={6}
          strokeLinecap="round"
          strokeDasharray={`${(s / 100) * c} ${c}`}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="num text-lg font-semibold leading-none">{score ? s.toFixed(0) : "—"}</span>
        <span className="mt-0.5 text-[9px] uppercase tracking-wider text-subtle">/ 100</span>
      </div>
    </div>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: "buy" | "sell" | "neutral" }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] font-medium uppercase tracking-wider text-subtle">{label}</div>
      <div className={clsx("num mt-1 truncate text-lg font-semibold", tone === "buy" && "text-buy", tone === "sell" && "text-sell")}>{value}</div>
      {sub && <div className="mt-0.5 truncate text-xs text-muted">{sub}</div>}
    </div>
  );
}

export function Empty({ title, body, action, icon }: { title: string; body?: React.ReactNode; action?: React.ReactNode; icon?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-10 text-center">
      {icon && <div className="mb-3 text-subtle">{icon}</div>}
      <div className="text-sm font-medium text-fg">{title}</div>
      {body && <div className="mt-1 max-w-md text-xs leading-relaxed text-muted">{body}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("animate-pulse rounded-md bg-surface-2", className)} />;
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="flex items-start gap-2 rounded-lg border border-sell/30 bg-sell-soft px-3 py-2 text-xs text-sell" role="alert">
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
      <span>{msg}</span>
    </div>
  );
}

export function Notice({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "neutral" | "wait" }) {
  return (
    <div className={clsx("flex items-start gap-2 rounded-lg border px-3 py-2 text-xs leading-relaxed", tone === "wait" ? "border-wait/30 bg-wait-soft text-wait" : "border-line bg-surface-2 text-muted")}>
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
      <div>{children}</div>
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-fg">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function TableWrap({ children }: { children: React.ReactNode }) {
  return <div className="scrollbar-thin overflow-x-auto">{children}</div>;
}

export const th = "whitespace-nowrap px-3 py-2 text-left text-[11px] font-medium uppercase tracking-wider text-subtle";
export const td = "whitespace-nowrap px-3 py-2.5 text-sm";

export function AssetLink({ id, symbol }: { id: number; symbol: string }) {
  return (
    <Link href={`/charts?asset=${id}`} className="font-medium text-fg hover:text-accent">
      {symbol}
    </Link>
  );
}

export function Delta({ v, children }: { v: number | null | undefined; children: React.ReactNode }) {
  return <span className={clsx("num", v == null ? "text-subtle" : v > 0 ? "text-buy" : v < 0 ? "text-sell" : "text-muted")}>{children}</span>;
}
