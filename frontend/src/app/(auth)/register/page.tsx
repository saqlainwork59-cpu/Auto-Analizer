"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button, ErrorNote, Field, Input } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/providers";

function policy(pw: string): string[] {
  const out: string[] = [];
  if (pw.length < 10) out.push("at least 10 characters");
  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((r) => r.test(pw)).length;
  if (classes < 3) out.push("3 of: lowercase, uppercase, digit, symbol");
  return out;
}

export default function RegisterPage() {
  const router = useRouter();
  const { refresh } = useAuth();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [ack, setAck] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const missing = policy(password);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await api("/api/auth/register", { method: "POST", json: { email, password, display_name: name || null } });
      await refresh();
      router.replace("/dashboard");
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      <div>
        <h2 className="text-xl font-semibold tracking-tight">Create account</h2>
        <p className="mt-1 text-sm text-muted">Paper trading starts with a virtual $10,000 balance.</p>
      </div>
      <Field label="Display name (optional)">
        <Input value={name} maxLength={80} onChange={(e) => setName(e.target.value)} autoComplete="nickname" />
      </Field>
      <Field label="Email">
        <Input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
      </Field>
      <Field label="Password" hint={password && missing.length ? `Needs ${missing.join(" and ")}` : "10+ characters, mixed character types"}>
        <Input type="password" autoComplete="new-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
      </Field>
      <label className="flex items-start gap-2 text-xs leading-relaxed text-muted">
        <input type="checkbox" className="mt-0.5 accent-[var(--accent)]" checked={ack} onChange={(e) => setAck(e.target.checked)} />
        <span>I understand Parallax provides probabilistic analysis, not financial advice, and that trading involves risk of loss.</span>
      </label>
      <ErrorNote error={error} />
      <Button variant="primary" className="w-full" loading={loading} disabled={!ack || missing.length > 0 || !email} type="submit">
        Create account
      </Button>
      <p className="text-center text-sm text-muted">
        Already registered?{" "}
        <Link href="/login" className="font-medium text-accent hover:underline">
          Sign in
        </Link>
      </p>
    </form>
  );
}
