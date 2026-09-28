"use client";

import clsx from "clsx";
import { AlertOctagon, CheckCircle2, CircleSlash, Database, Server, Workflow } from "lucide-react";
import { useState } from "react";
import useSWR, { mutate } from "swr";
import { Button, Card, CardHeader, Empty, ErrorNote, Field, Input, Notice, PageHeader, Pill, Segmented, Select, Skeleton, TableWrap, Toggle, td, th } from "@/components/ui";
import { api } from "@/lib/api";
import { ago, dateTime, num } from "@/lib/format";
import { useAuth } from "@/lib/providers";

interface Flag { key: string; enabled: boolean; description: string; reason: string | null; env_allows?: boolean }
interface Status {
  environment: string;
  database: string;
  bus: { kind: string; ok: boolean };
  worker: { ok: boolean; heartbeat: { ts: string; queue: number } | null };
  providers: Record<string, { configured: boolean }>;
  feeds: { id: string; provider: string; status: string; last_message_at: string | null; last_error: string | null }[];
  flags: Flag[];
  signals_24h: Record<string, number>;
  last_signal_at: string | null;
  errors_24h: number;
  users: { total: number; active_7d: number };
  open_paper_trades: number;
  backtests_running: number;
}

function Health({ label, ok, detail, Icon }: { label: string; ok: boolean; detail: string; Icon: typeof Server }) {
  return (
    <Card className="flex items-center gap-3 p-4">
      <div className={clsx("rounded-lg p-2", ok ? "bg-buy-soft text-buy" : "bg-sell-soft text-sell")}>
        <Icon className="h-4 w-4" aria-hidden />
      </div>
      <div className="min-w-0">
        <div className="text-xs text-subtle">{label}</div>
        <div className="flex items-center gap-1.5 text-sm font-medium">
          {ok ? <CheckCircle2 className="h-3.5 w-3.5 text-buy" aria-hidden /> : <AlertOctagon className="h-3.5 w-3.5 text-sell" aria-hidden />}
          {ok ? "Operational" : "Problem"}
        </div>
        <div className="truncate text-[11px] text-muted">{detail}</div>
      </div>
    </Card>
  );
}

function Controls({ flags }: { flags: Flag[] }) {
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<unknown>(null);
  async function set(key: string, enabled: boolean) {
    setErr(null);
    try {
      await api(`/api/admin/flags/${encodeURIComponent(key)}`, { method: "POST", json: { enabled, reason: reason || null } });
      mutate("/api/admin/status");
    } catch (e) {
      setErr(e);
    }
  }
  return (
    <Card>
      <CardHeader title="Emergency controls" subtitle="Take effect across API and worker within ~5 seconds. Every change is audit-logged." />
      <div className="border-b border-line p-4">
        <Field label="Reason (recorded in the audit log)">
          <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. provider incident" maxLength={300} />
        </Field>
      </div>
      <ul className="divide-y divide-line">
        {flags.map((f) => (
          <li key={f.key} className="flex items-center gap-3 px-4 py-3">
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium">{f.key}</div>
              <div className="text-xs text-muted">{f.description}</div>
              {f.reason && <div className="text-[11px] text-subtle">Last reason: {f.reason}</div>}
              {f.env_allows === false && <div className="text-[11px] text-wait">Locked off by BROKER_EXECUTION_ENABLED=false</div>}
            </div>
            <Pill tone={f.enabled ? "buy" : "sell"}>{f.enabled ? "ON" : "OFF"}</Pill>
            <Toggle checked={f.enabled} onChange={(v) => set(f.key, v)} label={f.key} disabled={f.env_allows === false} />
          </li>
        ))}
      </ul>
      <div className="p-3">
        <ErrorNote error={err} />
      </div>
    </Card>
  );
}

function Overview() {
  const { data: s, error } = useSWR<Status>("/api/admin/status", { refreshInterval: 10_000 });
  if (error) return <ErrorNote error={error} />;
  if (!s) return <Skeleton className="h-96" />;
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Health label="Database" ok={s.database === "ok"} detail="PostgreSQL" Icon={Database} />
        <Health label="Real-time bus" ok={s.bus.ok} detail={s.bus.kind === "redis" ? "Redis pub/sub" : "in-process fallback (single node)"} Icon={Workflow} />
        <Health label="Worker / signal engine" ok={s.worker.ok} detail={s.worker.heartbeat ? `heartbeat ${ago(s.worker.heartbeat.ts)} · queue ${s.worker.heartbeat.queue}` : "no heartbeat"} Icon={Server} />
        <Health label="Errors (24h)" ok={s.errors_24h === 0} detail={`${s.errors_24h} error log entries`} Icon={AlertOctagon} />
      </div>
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <Controls flags={s.flags} />
        <div className="space-y-5">
          <Card>
            <CardHeader title="Data feeds" />
            <TableWrap>
              <table className="w-full min-w-[520px]">
                <thead className="border-b border-line">
                  <tr>
                    <th className={th}>Feed</th>
                    <th className={th}>Status</th>
                    <th className={th}>Last message</th>
                    <th className={th}>Last error</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {s.feeds.map((f) => (
                    <tr key={f.id}>
                      <td className={clsx(td, "text-xs font-medium")}>{f.id}</td>
                      <td className={td}>
                        <Pill tone={f.status === "ok" ? "buy" : f.status === "down" || f.status === "degraded" ? "sell" : "neutral"}>{f.status}</Pill>
                      </td>
                      <td className={clsx(td, "text-xs text-muted")}>{ago(f.last_message_at)}</td>
                      <td className={clsx(td, "max-w-[260px] truncate text-xs text-muted")} title={f.last_error || undefined}>
                        {f.last_error || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
            <div className="flex flex-wrap gap-2 border-t border-line p-3 text-xs">
              {Object.entries(s.providers).map(([k, v]) => (
                <Pill key={k} tone={v.configured ? "buy" : "neutral"}>
                  {k}: {v.configured ? "configured" : "no credentials"}
                </Pill>
              ))}
            </div>
          </Card>
          <Card>
            <CardHeader title="Activity" />
            <dl className="grid grid-cols-2 gap-3 p-4 text-sm sm:grid-cols-3">
              {Object.entries(s.signals_24h).map(([k, v]) => (
                <div key={k}>
                  <dt className="text-[11px] uppercase tracking-wider text-subtle">{k} (24h)</dt>
                  <dd className="num font-semibold">{v}</dd>
                </div>
              ))}
              <div>
                <dt className="text-[11px] uppercase tracking-wider text-subtle">Last signal</dt>
                <dd className="text-xs">{ago(s.last_signal_at)}</dd>
              </div>
              <div>
                <dt className="text-[11px] uppercase tracking-wider text-subtle">Users (active 7d)</dt>
                <dd className="num font-semibold">
                  {s.users.total} ({s.users.active_7d})
                </dd>
              </div>
              <div>
                <dt className="text-[11px] uppercase tracking-wider text-subtle">Open paper trades</dt>
                <dd className="num font-semibold">{s.open_paper_trades}</dd>
              </div>
              <div>
                <dt className="text-[11px] uppercase tracking-wider text-subtle">Backtests queued</dt>
                <dd className="num font-semibold">{s.backtests_running}</dd>
              </div>
            </dl>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Logs() {
  const [level, setLevel] = useState("");
  const { data } = useSWR<{ id: number; created_at: string; level: string; source: string; message: string }[]>(`/api/admin/logs?limit=300${level ? `&level=${level}` : ""}`, { refreshInterval: 15_000 });
  const { data: audit } = useSWR<{ id: number; created_at: string; user_id: number | null; action: string; target: string | null; ip: string | null }[]>("/api/admin/audit?limit=200");
  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
      <Card>
        <CardHeader
          title="System log"
          action={
            <Select value={level} onChange={(e) => setLevel(e.target.value)} className="h-8 w-32 text-xs" aria-label="Level">
              <option value="">All levels</option>
              <option>ERROR</option>
              <option>WARNING</option>
              <option>INFO</option>
            </Select>
          }
        />
        <ul className="scrollbar-thin max-h-[560px] divide-y divide-line overflow-y-auto font-mono text-[11px]">
          {(data || []).map((l) => (
            <li key={l.id} className="px-4 py-1.5">
              <span className="text-subtle">{dateTime(l.created_at)}</span>{" "}
              <span className={l.level === "ERROR" ? "text-sell" : l.level === "WARNING" ? "text-wait" : "text-muted"}>{l.level}</span>{" "}
              <span className="text-accent">[{l.source}]</span> {l.message}
            </li>
          ))}
        </ul>
      </Card>
      <Card>
        <CardHeader title="Audit log" subtitle="Security-relevant actions (logins, flags, promotions, credential changes)" />
        <ul className="scrollbar-thin max-h-[560px] divide-y divide-line overflow-y-auto text-xs">
          {(audit || []).map((a) => (
            <li key={a.id} className="flex gap-3 px-4 py-1.5">
              <span className="w-28 shrink-0 text-subtle">{dateTime(a.created_at)}</span>
              <span className="font-medium">{a.action}</span>
              <span className="truncate text-muted">
                {a.target || ""} {a.user_id ? `· user ${a.user_id}` : ""} {a.ip ? `· ${a.ip}` : ""}
              </span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

function Research() {
  const { data: models } = useSWR<{ id: number; name: string; version: number; status: string; algorithm: string; created_at: string; metrics: { holdout?: Record<string, number | null> }; overfit: { passes_gate?: boolean; checks?: { check: string; passed: boolean; detail: string }[] }; dataset: { rows?: number; assets?: string[] } }[]>("/api/admin/models", { refreshInterval: 15_000 });
  const { data: strategies } = useSWR<{ id: number; version: number; status: string; notes: string | null; validation: { passes?: boolean } | null; created_at: string; params: { weights: Record<string, number>; min_score: number; min_rr: number } }[]>("/api/admin/strategies");
  const { data: jobs } = useSWR<{ id: number; kind: string; status: string; error: string | null; created_at: string }[]>("/api/admin/jobs", { refreshInterval: 5000 });
  const [tf, setTf] = useState("1h");
  const [err, setErr] = useState<unknown>(null);
  async function job(kind: string) {
    setErr(null);
    try {
      await api("/api/admin/jobs", { method: "POST", json: { kind, timeframe: tf } });
      mutate("/api/admin/jobs");
    } catch (e) {
      setErr(e);
    }
  }
  async function promote(id: number) {
    setErr(null);
    try {
      await api(`/api/admin/models/${id}/promote`, { method: "POST" });
      mutate("/api/admin/models");
    } catch (e) {
      setErr(e);
    }
  }
  async function retire(id: number) {
    await api(`/api/admin/models/${id}/retire`, { method: "POST" });
    mutate("/api/admin/models");
  }
  async function activate(id: number) {
    setErr(null);
    try {
      await api(`/api/admin/strategies/${id}/activate`, { method: "POST", json: {} });
      mutate("/api/admin/strategies");
    } catch (e) {
      setErr(e);
    }
  }
  return (
    <div className="space-y-5">
      <Notice>
        Research runs on stored history with purged walk-forward splits and an untouched holdout. Nothing reaches production automatically: models and weight sets can only be
        promoted by an administrator, and only if they passed out-of-sample validation.
      </Notice>
      <Card>
        <CardHeader title="Run research" />
        <div className="flex flex-wrap items-end gap-3 p-4">
          <Field label="Timeframe">
            <Select value={tf} onChange={(e) => setTf(e.target.value)} className="w-28">
              {["15m", "1h", "4h"].map((t) => (
                <option key={t}>{t}</option>
              ))}
            </Select>
          </Field>
          <Button onClick={() => job("ml_train")}>Train outcome model</Button>
          <Button onClick={() => job("optimize_weights")}>Optimise score weights</Button>
        </div>
        <div className="px-4 pb-3">
          <ErrorNote error={err} />
        </div>
        <ul className="divide-y divide-line border-t border-line text-xs">
          {(jobs || []).slice(0, 8).map((j) => (
            <li key={j.id} className="flex gap-3 px-4 py-2">
              <span className="w-8 text-subtle">#{j.id}</span>
              <span className="font-medium">{j.kind}</span>
              <Pill tone={j.status === "done" ? "buy" : j.status === "failed" ? "sell" : "accent"}>{j.status}</Pill>
              <span className="truncate text-muted">{j.error || dateTime(j.created_at)}</span>
            </li>
          ))}
        </ul>
      </Card>
      <Card>
        <CardHeader title="Model versions" />
        {models && models.length === 0 && <Empty icon={<CircleSlash className="h-6 w-6" />} title="No models trained" body="The engine runs purely on transparent rules until a model passes validation and is promoted." />}
        <div className="divide-y divide-line">
          {(models || []).map((m) => (
            <div key={m.id} className="px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium">
                  {m.name} v{m.version}
                </span>
                <Pill tone={m.status === "production" ? "buy" : m.status === "rejected" ? "sell" : "neutral"}>{m.status}</Pill>
                <span className="text-xs text-muted">
                  {m.algorithm} · {m.dataset.rows} rows · {dateTime(m.created_at)}
                </span>
                <div className="ml-auto flex gap-2">
                  {m.status === "candidate" && (
                    <Button size="sm" variant="primary" onClick={() => promote(m.id)}>
                      Promote
                    </Button>
                  )}
                  {m.status === "production" && (
                    <Button size="sm" onClick={() => retire(m.id)}>
                      Retire
                    </Button>
                  )}
                </div>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {(m.overfit.checks || []).map((c) => (
                  <Pill key={c.check} tone={c.passed ? "buy" : "sell"}>
                    {c.passed ? "✓" : "✗"} {c.check}
                  </Pill>
                ))}
              </div>
              <div className="num mt-1.5 text-[11px] text-muted">
                holdout AUC {num(m.metrics.holdout?.auc as number, 3)} · Brier {num(m.metrics.holdout?.brier as number, 3)} · expectancy all {num(m.metrics.holdout?.expectancy_all_r as number, 3)}R → filtered{" "}
                {num(m.metrics.holdout?.expectancy_filtered_r as number, 3)}R
              </div>
            </div>
          ))}
        </div>
      </Card>
      <Card>
        <CardHeader title="Strategy versions" subtitle="Weights & thresholds used by the signal engine" />
        <TableWrap>
          <table className="w-full min-w-[720px]">
            <thead className="border-b border-line">
              <tr>
                {["Version", "Status", "Validated", "Min score", "Min R:R", "Weights", ""].map((h) => (
                  <th key={h} className={th}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {(strategies || []).map((s) => (
                <tr key={s.id}>
                  <td className={clsx(td, "text-xs font-medium")}>v{s.version}</td>
                  <td className={td}>
                    <Pill tone={s.status === "active" ? "buy" : "neutral"}>{s.status}</Pill>
                  </td>
                  <td className={clsx(td, "text-xs")}>{s.validation ? (s.validation.passes ? "passed holdout" : "failed holdout") : "—"}</td>
                  <td className={clsx(td, "num text-xs")}>{s.params.min_score}</td>
                  <td className={clsx(td, "num text-xs")}>{s.params.min_rr}</td>
                  <td className={clsx(td, "max-w-[320px] truncate text-[11px] text-muted")}>
                    {Object.entries(s.params.weights)
                      .map(([k, v]) => `${k.split("_")[0]} ${Math.round(v)}`)
                      .join(" · ")}
                  </td>
                  <td className={td}>
                    {s.status !== "active" && s.validation?.passes && (
                      <Button size="sm" onClick={() => activate(s.id)}>
                        Activate
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      </Card>
    </div>
  );
}

function Users() {
  const { data } = useSWR<{ id: number; email: string; role: string; is_active: boolean; created_at: string; last_login_at: string | null }[]>("/api/admin/users");
  const { user } = useAuth();
  const [err, setErr] = useState<unknown>(null);
  async function patch(id: number, body: object) {
    setErr(null);
    try {
      await api(`/api/admin/users/${id}`, { method: "PATCH", json: body });
      mutate("/api/admin/users");
    } catch (e) {
      setErr(e);
    }
  }
  return (
    <Card>
      <CardHeader title="Users" />
      <div className="px-4 pt-3">
        <ErrorNote error={err} />
      </div>
      <TableWrap>
        <table className="w-full min-w-[640px]">
          <thead className="border-b border-line">
            <tr>
              {["Email", "Role", "Active", "Created", "Last login"].map((h) => (
                <th key={h} className={th}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {(data || []).map((u) => (
              <tr key={u.id}>
                <td className={clsx(td, "text-xs")}>{u.email}</td>
                <td className={td}>
                  <Select value={u.role} disabled={u.id === user?.id} onChange={(e) => patch(u.id, { role: e.target.value })} className="h-8 w-24 text-xs" aria-label="Role">
                    <option value="user">user</option>
                    <option value="admin">admin</option>
                  </Select>
                </td>
                <td className={td}>
                  <Toggle checked={u.is_active} disabled={u.id === user?.id} onChange={(v) => patch(u.id, { is_active: v })} label="Active" />
                </td>
                <td className={clsx(td, "text-xs text-muted")}>{dateTime(u.created_at)}</td>
                <td className={clsx(td, "text-xs text-muted")}>{ago(u.last_login_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
    </Card>
  );
}

export default function AdminPage() {
  const { user } = useAuth();
  const [tab, setTab] = useState("overview");
  if (user?.role !== "admin") return <Empty title="Administrators only" body="Your account does not have access to this area." />;
  return (
    <div>
      <PageHeader
        title="Admin"
        subtitle="System health, emergency controls, logs, research and users."
        actions={
          <Segmented
            value={tab}
            onChange={setTab}
            options={[
              { value: "overview", label: "Overview" },
              { value: "logs", label: "Logs" },
              { value: "research", label: "Models & strategy" },
              { value: "users", label: "Users" },
            ]}
          />
        }
      />
      {tab === "overview" && <Overview />}
      {tab === "logs" && <Logs />}
      {tab === "research" && <Research />}
      {tab === "users" && <Users />}
    </div>
  );
}
