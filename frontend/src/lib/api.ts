/**
 * Browser API client. Auth lives in httpOnly cookies (never readable by JS); the CSRF token is a
 * separate readable cookie echoed in X-CSRF-Token on mutations. On a 401 we try one silent refresh.
 */
export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

function csrf(): string {
  if (typeof document === "undefined") return "";
  const m = document.cookie.match(/(?:^|; )px_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

let refreshing: Promise<boolean> | null = null;

async function refresh(): Promise<boolean> {
  if (!refreshing) {
    refreshing = fetch("/api/auth/refresh", { method: "POST", credentials: "same-origin" })
      .then((r) => r.ok)
      .catch(() => false)
      .finally(() => {
        setTimeout(() => (refreshing = null), 0);
      });
  }
  return refreshing;
}

function messageFrom(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((e) => (e && typeof e === "object" && "msg" in e ? String(e.msg) : String(e))).join("; ");
  }
  if (body && typeof body === "object" && "errors" in body) {
    const errs = (body as { errors: { loc?: unknown[]; msg: string }[] }).errors;
    return errs.map((e) => `${(e.loc || []).slice(-1)[0] ?? "field"}: ${e.msg}`).join("; ");
  }
  return status === 0 ? "Network error - API unreachable" : `Request failed (${status})`;
}

export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown } = {}, retry = true): Promise<T> {
  const headers = new Headers(init.headers);
  const method = (init.method || "GET").toUpperCase();
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  if (method !== "GET") headers.set("X-CSRF-Token", csrf());
  let res: Response;
  try {
    res = await fetch(path, { ...init, method, headers, body, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "Network error - API unreachable");
  }
  if (res.status === 401 && retry && !path.startsWith("/api/auth/")) {
    if (await refresh()) return api<T>(path, init, false);
  }
  const text = await res.text();
  let parsed: unknown = null;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = text;
  }
  if (!res.ok) throw new ApiError(res.status, messageFrom(parsed, res.status), parsed);
  return parsed as T;
}

export const fetcher = <T,>(path: string) => api<T>(path);
