import { CheckCircle2 } from "lucide-react";
import { Logo } from "./Shell";

const PRINCIPLES = [
  ["Multi-timeframe evidence", "Every setup is checked against higher-timeframe structure up to 1D. Conflicts produce WAIT, not a forced trade."],
  ["Transparent scoring", "Seven weighted components, each backed by the computed values behind it."],
  ["Levels from structure", "Entries, stops and targets come from swings, zones and ATR — never fixed percentages."],
  ["Auditable history", "Every signal is stored and tracked to its outcome, losses included."],
];

export function AuthFrame({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-dvh lg:grid-cols-[1.05fr_1fr]">
      <section className="relative hidden overflow-hidden border-r border-line bg-surface lg:flex lg:flex-col lg:justify-between lg:p-12">
        <Logo />
        <div className="max-w-md">
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-accent">Analysis engine</p>
          <h1 className="mt-3 text-3xl font-semibold leading-tight tracking-tight">
            High-quality setups when the evidence lines up. <span className="text-muted">WAIT when it doesn&apos;t.</span>
          </h1>
          <ul className="mt-8 space-y-4">
            {PRINCIPLES.map(([t, d]) => (
              <li key={t} className="flex gap-3">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-accent" aria-hidden />
                <div>
                  <div className="text-sm font-medium">{t}</div>
                  <div className="mt-0.5 text-sm leading-relaxed text-muted">{d}</div>
                </div>
              </li>
            ))}
          </ul>
        </div>
        <p className="max-w-md text-xs leading-relaxed text-subtle">
          Parallax provides analysis, not financial advice. No system can guarantee profits or prevent losses; setups are
          probabilistic and backtests describe the past, not the future.
        </p>
        <svg className="pointer-events-none absolute -right-24 bottom-24 opacity-[0.06]" width="520" height="260" viewBox="0 0 520 260" aria-hidden>
          <path d="M0 220 L60 180 L110 200 L170 120 L230 150 L290 80 L350 110 L410 40 L470 70 L520 20" stroke="var(--fg)" strokeWidth="3" fill="none" />
        </svg>
      </section>
      <section className="flex items-center justify-center px-5 py-10">
        <div className="w-full max-w-sm">
          <div className="mb-8 lg:hidden">
            <Logo />
          </div>
          {children}
        </div>
      </section>
    </div>
  );
}
