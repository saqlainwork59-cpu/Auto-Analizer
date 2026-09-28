"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import useSWR, { SWRConfig, mutate as globalMutate } from "swr";
import { api, ApiError, fetcher } from "./api";
import type { User } from "./types";

/* ------------------------------------------------------------------ theme */
type ThemePref = "light" | "dark" | "system";
const ThemeCtx = createContext<{ pref: ThemePref; resolved: "light" | "dark"; setPref: (p: ThemePref) => void }>({
  pref: "system",
  resolved: "dark",
  setPref: () => {},
});

function readPref(): ThemePref {
  try {
    const v = localStorage.getItem("px-theme");
    if (v === "light" || v === "dark" || v === "system") return v;
  } catch {}
  return "system";
}

function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [pref, setPrefState] = useState<ThemePref>("system");
  const [resolved, setResolved] = useState<"light" | "dark">("dark");
  useEffect(() => setPrefState(readPref()), []);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      const dark = pref === "dark" || (pref === "system" && mq.matches);
      document.documentElement.classList.toggle("dark", dark);
      setResolved(dark ? "dark" : "light");
    };
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [pref]);
  const setPref = useCallback((p: ThemePref) => {
    try {
      localStorage.setItem("px-theme", p);
    } catch {}
    setPrefState(p);
  }, []);
  return <ThemeCtx.Provider value={{ pref, resolved, setPref }}>{children}</ThemeCtx.Provider>;
}

export const useTheme = () => useContext(ThemeCtx);

/* ------------------------------------------------------------------ auth */
const AuthCtx = createContext<{
  user: User | null | undefined;
  refresh: () => Promise<unknown>;
  logout: () => Promise<void>;
}>({ user: undefined, refresh: async () => {}, logout: async () => {} });

function AuthProvider({ children }: { children: React.ReactNode }) {
  const { data, error, mutate } = useSWR<User>("/api/auth/me", fetcher, { shouldRetryOnError: false, revalidateOnFocus: true });
  const user = error instanceof ApiError && error.status === 401 ? null : error ? null : data;
  const logout = useCallback(async () => {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } finally {
      await globalMutate(() => true, undefined, { revalidate: false });
      window.location.href = "/login";
    }
  }, []);
  return <AuthCtx.Provider value={{ user: data ? data : user, refresh: () => mutate(), logout }}>{children}</AuthCtx.Provider>;
}

export const useAuth = () => useContext(AuthCtx);

/* ------------------------------------------------------------------ live (WebSocket) */
export type LiveMessage = { type: string; [k: string]: unknown };
type Listener = (m: LiveMessage) => void;

const LiveCtx = createContext<{
  status: "connecting" | "live" | "offline";
  subscribe: (assetIds: number[]) => () => void;
  on: (fn: Listener) => () => void;
}>({ status: "offline", subscribe: () => () => {}, on: () => () => {} });

function LiveProvider({ children, enabled }: { children: React.ReactNode; enabled: boolean }) {
  const [status, setStatus] = useState<"connecting" | "live" | "offline">("offline");
  const wsRef = useRef<WebSocket | null>(null);
  const listeners = useRef(new Set<Listener>());
  const counts = useRef(new Map<number, number>());

  const sendSubs = useCallback(() => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: "subscribe", asset_ids: [...counts.current.keys()] }));
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    let stop = false;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const connect = () => {
      if (stop) return;
      setStatus("connecting");
      const base = process.env.NEXT_PUBLIC_WS_URL || `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/ws`;
      const ws = new WebSocket(base);
      wsRef.current = ws;
      ws.onopen = () => {
        attempt = 0;
        setStatus("live");
        sendSubs();
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data) as LiveMessage;
          listeners.current.forEach((fn) => fn(msg));
          if (msg.type === "notify" && typeof Notification !== "undefined" && Notification.permission === "granted") {
            const n = new Notification(String(msg.title || "Parallax"), { body: String(msg.body || "") });
            if (msg.signal_id) n.onclick = () => window.open(`/signals/${msg.signal_id}`, "_blank");
          }
          if (msg.type === "signal") globalMutate((k) => typeof k === "string" && (k.startsWith("/api/signals") || k.startsWith("/api/markets")));
          if (msg.type === "paper") globalMutate((k) => typeof k === "string" && k.startsWith("/api/paper"));
        } catch {}
      };
      ws.onclose = async (ev) => {
        setStatus("offline");
        if (stop) return;
        if (ev.code === 4401) {
          try {
            await api("/api/auth/refresh", { method: "POST" });
          } catch {}
        }
        attempt += 1;
        timer = setTimeout(connect, Math.min(30000, 1000 * 2 ** Math.min(attempt, 5)));
      };
    };
    connect();
    const ping = setInterval(() => wsRef.current?.readyState === WebSocket.OPEN && wsRef.current.send('{"action":"ping"}'), 25000);
    return () => {
      stop = true;
      clearInterval(ping);
      if (timer) clearTimeout(timer);
      wsRef.current?.close();
    };
  }, [enabled, sendSubs]);

  const subscribe = useCallback(
    (ids: number[]) => {
      ids.forEach((id) => counts.current.set(id, (counts.current.get(id) || 0) + 1));
      sendSubs();
      return () => {
        const gone: number[] = [];
        ids.forEach((id) => {
          const n = (counts.current.get(id) || 1) - 1;
          if (n <= 0) {
            counts.current.delete(id);
            gone.push(id);
          } else counts.current.set(id, n);
        });
        const ws = wsRef.current;
        if (gone.length && ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action: "unsubscribe", asset_ids: gone }));
      };
    },
    [sendSubs],
  );
  const on = useCallback((fn: Listener) => {
    listeners.current.add(fn);
    return () => {
      listeners.current.delete(fn);
    };
  }, []);
  const value = useMemo(() => ({ status, subscribe, on }), [status, subscribe, on]);
  return <LiveCtx.Provider value={value}>{children}</LiveCtx.Provider>;
}

export const useLive = () => useContext(LiveCtx);

function LiveGate({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  return <LiveProvider enabled={!!user}>{children}</LiveProvider>;
}

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <SWRConfig value={{ fetcher, revalidateOnFocus: false, dedupingInterval: 2000 }}>
      <ThemeProvider>
        <AuthProvider>
          <LiveGate>{children}</LiveGate>
        </AuthProvider>
      </ThemeProvider>
    </SWRConfig>
  );
}
