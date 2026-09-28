"use client";

import clsx from "clsx";
import {
  Activity,
  BarChart3,
  CandlestickChart,
  FlaskConical,
  Gauge,
  LayoutDashboard,
  LineChart,
  LogOut,
  Menu,
  Monitor,
  Moon,
  Settings,
  Shield,
  Star,
  Sun,
  Wallet,
  X,
  Zap,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { useAuth, useLive, useTheme } from "@/lib/providers";

const NAV = [
  { href: "/dashboard", label: "Dashboard", Icon: LayoutDashboard },
  { href: "/markets", label: "Markets", Icon: BarChart3 },
  { href: "/signals", label: "AI Signals", Icon: Zap },
  { href: "/charts", label: "Charts", Icon: CandlestickChart },
  { href: "/watchlist", label: "Watchlist", Icon: Star },
  { href: "/backtesting", label: "Backtesting", Icon: FlaskConical },
  { href: "/paper", label: "Paper Trading", Icon: Wallet },
  { href: "/performance", label: "Performance", Icon: LineChart },
  { href: "/settings", label: "Settings", Icon: Settings },
];

export function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2">
      <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden>
        <rect x="0.5" y="0.5" width="25" height="25" rx="7" fill="var(--fg)" />
        <path d="M7 17.5 L11 12 L14 14.5 L19 8" stroke="var(--bg)" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="19" cy="8" r="1.8" fill="var(--accent)" />
      </svg>
      {!compact && (
        <div className="leading-none">
          <div className="text-[15px] font-semibold tracking-tight">Parallax</div>
          <div className="mt-0.5 text-[10px] uppercase tracking-[0.14em] text-subtle">Market analysis</div>
        </div>
      )}
    </div>
  );
}

function ThemeSwitch() {
  const { pref, setPref } = useTheme();
  const opts = [
    { v: "light" as const, Icon: Sun, label: "Light theme" },
    { v: "system" as const, Icon: Monitor, label: "System theme" },
    { v: "dark" as const, Icon: Moon, label: "Dark theme" },
  ];
  return (
    <div className="inline-flex rounded-lg border border-line bg-surface-2 p-0.5">
      {opts.map(({ v, Icon, label }) => (
        <button
          key={v}
          onClick={() => setPref(v)}
          aria-label={label}
          aria-pressed={pref === v}
          className={clsx("rounded-md p-1.5", pref === v ? "bg-surface text-fg shadow-card" : "text-subtle hover:text-fg")}
        >
          <Icon className="h-3.5 w-3.5" />
        </button>
      ))}
    </div>
  );
}

function LiveIndicator() {
  const { status } = useLive();
  const label = status === "live" ? "Live" : status === "connecting" ? "Connecting" : "Offline";
  return (
    <div className="flex items-center gap-1.5 rounded-lg border border-line px-2 py-1 text-[11px] text-muted" title="Real-time connection to the analysis server">
      <span
        className={clsx("h-1.5 w-1.5 rounded-full", status === "live" ? "bg-buy pulse-dot" : status === "connecting" ? "bg-wait" : "bg-na")}
        aria-hidden
      />
      {label}
    </div>
  );
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const path = usePathname();
  const { user } = useAuth();
  const items = user?.role === "admin" ? [...NAV, { href: "/admin", label: "Admin", Icon: Shield }] : NAV;
  return (
    <nav className="flex flex-col gap-0.5" aria-label="Main">
      {items.map(({ href, label, Icon }) => {
        const active = path === href || path.startsWith(href + "/");
        return (
          <Link
            key={href}
            href={href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={clsx(
              "group flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] font-medium transition-colors",
              active ? "bg-surface-2 text-fg" : "text-muted hover:bg-surface-2/60 hover:text-fg",
            )}
          >
            <Icon className={clsx("h-4 w-4", active ? "text-accent" : "text-subtle group-hover:text-muted")} aria-hidden />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  return (
    <div className="min-h-dvh">
      {/* desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 flex-col border-r border-line bg-surface lg:flex">
        <div className="flex h-14 items-center border-b border-line px-4">
          <Logo />
        </div>
        <div className="scrollbar-thin flex-1 overflow-y-auto px-3 py-3">
          <NavLinks />
        </div>
        <div className="border-t border-line p-3 text-[11px] leading-relaxed text-subtle">
          <div className="flex items-center gap-1.5 font-medium text-muted">
            <Gauge className="h-3.5 w-3.5" aria-hidden /> Analysis, not advice
          </div>
          Setups are probabilistic and can fail. Real-money execution is disabled.
        </div>
      </aside>

      {/* mobile drawer */}
      {open && (
        <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <div className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} />
          <div className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col border-r border-line bg-surface">
            <div className="flex h-14 items-center justify-between border-b border-line px-4">
              <Logo />
              <button onClick={() => setOpen(false)} aria-label="Close menu" className="rounded-md p-1.5 text-muted hover:bg-surface-2">
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-3">
              <NavLinks onNavigate={() => setOpen(false)} />
            </div>
          </div>
        </div>
      )}

      <div className="lg:pl-60">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-line bg-bg/85 px-4 backdrop-blur supports-[backdrop-filter]:bg-bg/70 sm:px-6">
          <button className="rounded-md p-1.5 text-muted hover:bg-surface-2 lg:hidden" onClick={() => setOpen(true)} aria-label="Open menu">
            <Menu className="h-5 w-5" />
          </button>
          <div className="lg:hidden">
            <Logo compact />
          </div>
          <div className="hidden items-center gap-2 text-xs text-muted md:flex">
            <Activity className="h-3.5 w-3.5" aria-hidden />
            Evidence-based setup detection · WAIT is a valid answer
          </div>
          <div className="ml-auto flex items-center gap-2">
            <LiveIndicator />
            <ThemeSwitch />
            {user && (
              <div className="hidden items-center gap-2 border-l border-line pl-3 sm:flex">
                <div className="text-right leading-tight">
                  <div className="max-w-[160px] truncate text-xs font-medium">{user.display_name || user.email}</div>
                  <div className="text-[10px] uppercase tracking-wider text-subtle">{user.role}</div>
                </div>
                <button onClick={logout} className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-fg" aria-label="Sign out" title="Sign out">
                  <LogOut className="h-4 w-4" />
                </button>
              </div>
            )}
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1600px] px-4 py-5 sm:px-6 sm:py-6">{children}</main>
      </div>
    </div>
  );
}
