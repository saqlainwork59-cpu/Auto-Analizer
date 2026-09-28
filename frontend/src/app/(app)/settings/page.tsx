"use client";

import { Bell, KeyRound, Lock, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import useSWR, { mutate } from "swr";
import { useMarkets } from "@/components/MarketBits";
import { Button, Card, CardHeader, ErrorNote, Field, Input, Notice, PageHeader, Segmented, Select, Toggle } from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime } from "@/lib/format";
import { useAuth, useTheme } from "@/lib/providers";
import type { TF } from "@/lib/types";

interface Rule {
  id: number;
  channel: string;
  destination_hint: string | null;
  is_enabled: boolean;
  min_score: number;
  asset_ids: number[] | null;
  timeframes: string[] | null;
}

function Profile() {
  const { user, refresh } = useAuth();
  const { pref, setPref } = useTheme();
  const [name, setName] = useState(user?.display_name || "");
  const [tf, setTf] = useState<TF>((user?.settings.default_timeframe as TF) || "1h");
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  async function save() {
    setErr(null);
    try {
      await api("/api/auth/me", { method: "PATCH", json: { display_name: name, default_timeframe: tf, theme: pref } });
      await refresh();
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setErr(e);
    }
  }
  return (
    <Card>
      <CardHeader title="Profile & display" />
      <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2">
        <Field label="Display name">
          <Input value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Email">
          <Input value={user?.email || ""} disabled />
        </Field>
        <Field label="Default chart timeframe">
          <Select value={tf} onChange={(e) => setTf(e.target.value as TF)}>
            {["5m", "15m", "1h", "4h", "1d"].map((t) => (
              <option key={t}>{t}</option>
            ))}
          </Select>
        </Field>
        <Field label="Theme">
          <div>
            <Segmented value={pref} onChange={setPref} options={[{ value: "light", label: "Light" }, { value: "system", label: "System" }, { value: "dark", label: "Dark" }]} />
          </div>
        </Field>
        <div className="sm:col-span-2">
          <ErrorNote error={err} />
        </div>
        <div className="sm:col-span-2">
          <Button variant="primary" onClick={save}>
            {saved ? "Saved" : "Save"}
          </Button>
        </div>
      </div>
    </Card>
  );
}

function Password() {
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  async function go(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setMsg(null);
    try {
      await api("/api/auth/change-password", { method: "POST", json: { current_password: cur, new_password: next } });
      setCur("");
      setNext("");
      setMsg("Password changed. Other sessions were signed out.");
    } catch (e2) {
      setErr(e2);
    }
  }
  return (
    <Card>
      <CardHeader title="Password" subtitle="Changing it revokes every other session" />
      <form onSubmit={go} className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2">
        <Field label="Current password">
          <Input type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} required />
        </Field>
        <Field label="New password" hint="10+ characters, 3 character types">
          <Input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required />
        </Field>
        <div className="sm:col-span-2">
          <ErrorNote error={err} />
          {msg && <p className="text-xs text-buy">{msg}</p>}
        </div>
        <div>
          <Button type="submit">
            <Lock className="h-3.5 w-3.5" /> Update password
          </Button>
        </div>
      </form>
    </Card>
  );
}

function Alerts() {
  const { data } = useSWR<{ rules: Rule[]; channels: Record<string, boolean> }>("/api/alerts");
  const { data: mk } = useMarkets();
  const [channel, setChannel] = useState("browser");
  const [dest, setDest] = useState("");
  const [minScore, setMinScore] = useState("75");
  const [tfs, setTfs] = useState<string[]>([]);
  const [err, setErr] = useState<unknown>(null);
  const [note, setNote] = useState<string | null>(null);
  const [perm, setPerm] = useState<string>("default");
  useEffect(() => {
    if (typeof Notification !== "undefined") setPerm(Notification.permission);
  }, []);
  const symbol = (id: number) => mk?.markets.find((m) => m.id === id)?.symbol || `#${id}`;

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      if (channel === "browser" && typeof Notification !== "undefined" && Notification.permission !== "granted") {
        setPerm(await Notification.requestPermission());
      }
      await api("/api/alerts", {
        method: "POST",
        json: { channel, destination: channel === "browser" ? null : dest, min_score: Number(minScore), timeframes: tfs.length ? tfs : null },
      });
      setDest("");
      mutate("/api/alerts");
    } catch (e2) {
      setErr(e2);
    }
  }
  async function patch(id: number, body: object) {
    await api(`/api/alerts/${id}`, { method: "PATCH", json: body });
    mutate("/api/alerts");
  }
  async function del(id: number) {
    await api(`/api/alerts/${id}`, { method: "DELETE" });
    mutate("/api/alerts");
  }
  async function test(id: number) {
    setNote(null);
    setErr(null);
    try {
      await api(`/api/alerts/${id}/test`, { method: "POST" });
      setNote("Test alert sent.");
    } catch (e) {
      setErr(e);
    }
  }

  return (
    <Card>
      <CardHeader title="Alerts" subtitle="Delivered once per signal when an actionable setup meets your minimum score. Rules without markets use your watchlist." />
      <div className="divide-y divide-line">
        {(data?.rules || []).map((a) => (
          <div key={a.id} className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm">
            <Bell className="h-4 w-4 text-subtle" aria-hidden />
            <div className="min-w-0 flex-1">
              <div className="font-medium capitalize">
                {a.channel} {a.destination_hint && <span className="num text-xs font-normal text-muted">{a.destination_hint}</span>}
              </div>
              <div className="text-xs text-muted">
                score ≥ {a.min_score} · {a.timeframes?.join(", ") || "all timeframes"} · {a.asset_ids ? a.asset_ids.map(symbol).join(", ") : "watchlist"}
              </div>
            </div>
            <Toggle checked={a.is_enabled} onChange={(v) => patch(a.id, { is_enabled: v })} label="Enabled" />
            <Button size="sm" onClick={() => test(a.id)}>
              Test
            </Button>
            <button onClick={() => del(a.id)} className="rounded p-1 text-subtle hover:text-sell" aria-label="Delete alert">
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        ))}
      </div>
      <form onSubmit={create} className="grid grid-cols-1 gap-3 border-t border-line p-4 sm:grid-cols-4">
        <Field label="Channel">
          <Select value={channel} onChange={(e) => setChannel(e.target.value)}>
            <option value="browser">Browser notification</option>
            <option value="telegram" disabled={data && !data.channels.telegram}>
              Telegram{data && !data.channels.telegram ? " (not configured)" : ""}
            </option>
            <option value="email" disabled={data && !data.channels.email}>
              Email{data && !data.channels.email ? " (not configured)" : ""}
            </option>
          </Select>
        </Field>
        {channel !== "browser" && (
          <Field label={channel === "telegram" ? "Telegram chat id" : "Email address"} hint={channel === "telegram" ? "Message the bot, then use your numeric chat id" : undefined}>
            <Input value={dest} onChange={(e) => setDest(e.target.value)} required />
          </Field>
        )}
        <Field label="Minimum score">
          <Input type="number" min={0} max={100} value={minScore} onChange={(e) => setMinScore(e.target.value)} />
        </Field>
        <Field label="Timeframes">
          <div className="flex h-9 items-center gap-1">
            {["15m", "1h", "4h"].map((t) => (
              <button
                type="button"
                key={t}
                aria-pressed={tfs.includes(t)}
                onClick={() => setTfs(tfs.includes(t) ? tfs.filter((x) => x !== t) : [...tfs, t])}
                className={`rounded-md border px-2 py-1 text-xs ${tfs.includes(t) ? "border-accent/40 bg-accent-soft text-accent" : "border-line text-muted"}`}
              >
                {t}
              </button>
            ))}
          </div>
        </Field>
        <div className="flex items-end">
          <Button type="submit" variant="primary">
            Add alert
          </Button>
        </div>
        <div className="sm:col-span-4">
          <ErrorNote error={err} />
          {note && <p className="text-xs text-buy">{note}</p>}
          {perm === "denied" && <p className="text-xs text-wait">Browser notifications are blocked for this site in your browser settings.</p>}
        </div>
      </form>
    </Card>
  );
}

function Credentials() {
  const { data } = useSWR<{ id: number; provider: string; label: string; key_hint: string; created_at: string }[]>("/api/credentials");
  const { data: broker } = useSWR<{ env_enabled: boolean; admin_enabled: boolean; message: string }>("/api/broker/status");
  const [f, setF] = useState({ provider: "", label: "", api_key: "", api_secret: "" });
  const [err, setErr] = useState<unknown>(null);
  async function add(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      await api("/api/credentials", { method: "POST", json: { ...f, api_secret: f.api_secret || null } });
      setF({ provider: "", label: "", api_key: "", api_secret: "" });
      mutate("/api/credentials");
    } catch (e2) {
      setErr(e2);
    }
  }
  async function del(id: number) {
    await api(`/api/credentials/${id}`, { method: "DELETE" });
    mutate("/api/credentials");
  }
  return (
    <Card>
      <CardHeader title="Broker connections" subtitle="Keys are encrypted server-side and never sent back to the browser" />
      <div className="p-4">
        <Notice tone="wait">{broker?.message || "Real-money execution is disabled."} Signals never place orders automatically; any future live order would require explicit per-order confirmation and password re-entry.</Notice>
      </div>
      <ul className="divide-y divide-line border-y border-line">
        {(data || []).map((c) => (
          <li key={c.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
            <KeyRound className="h-4 w-4 text-subtle" aria-hidden />
            <div className="flex-1">
              <div className="font-medium">{c.label}</div>
              <div className="text-xs text-muted">
                {c.provider} · <span className="num">{c.key_hint}</span> · added {dateTime(c.created_at)}
              </div>
            </div>
            <button onClick={() => del(c.id)} className="rounded p-1 text-subtle hover:text-sell" aria-label="Delete credential">
              <Trash2 className="h-4 w-4" />
            </button>
          </li>
        ))}
        {data && data.length === 0 && <li className="px-4 py-3 text-xs text-subtle">No credentials stored.</li>}
      </ul>
      <form onSubmit={add} className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2">
        <Field label="Provider id" hint="lowercase, e.g. alpaca-broker">
          <Input value={f.provider} onChange={(e) => setF({ ...f, provider: e.target.value })} required pattern="[a-z0-9_-]{2,40}" />
        </Field>
        <Field label="Label">
          <Input value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} required />
        </Field>
        <Field label="API key">
          <Input value={f.api_key} onChange={(e) => setF({ ...f, api_key: e.target.value })} required autoComplete="off" />
        </Field>
        <Field label="API secret (optional)">
          <Input type="password" value={f.api_secret} onChange={(e) => setF({ ...f, api_secret: e.target.value })} autoComplete="off" />
        </Field>
        <div className="sm:col-span-2">
          <ErrorNote error={err} />
        </div>
        <div>
          <Button type="submit">Store encrypted</Button>
        </div>
      </form>
    </Card>
  );
}

export default function SettingsPage() {
  return (
    <div>
      <PageHeader title="Settings" subtitle="Profile, security, alert delivery and broker connections." />
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <div className="space-y-5">
          <Profile />
          <Password />
        </div>
        <div className="space-y-5">
          <Alerts />
          <Credentials />
        </div>
      </div>
    </div>
  );
}
