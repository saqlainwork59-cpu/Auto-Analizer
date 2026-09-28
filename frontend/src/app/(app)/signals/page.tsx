"use client";

import { Radar } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { useMarkets } from "@/components/MarketBits";
import { SetupCard, SignalTable } from "@/components/signals";
import { Button, Card, CardHeader, Empty, PageHeader, Segmented, Select, Skeleton } from "@/components/ui";
import type { Signal } from "@/lib/types";

const PAGE = 50;

export default function SignalsPage() {
  const { data: mk } = useMarkets();
  const [status, setStatus] = useState<"ACTIONABLE" | "ALL" | "WAIT">("ACTIONABLE");
  const [asset, setAsset] = useState("");
  const [tf, setTf] = useState("");
  const [dir, setDir] = useState("");
  const [page, setPage] = useState(0);
  const qs = new URLSearchParams({ limit: String(PAGE), offset: String(page * PAGE) });
  if (status !== "ALL") qs.set("status", status);
  if (asset) qs.set("asset_id", asset);
  if (tf) qs.set("timeframe", tf);
  if (dir) qs.set("direction", dir);
  const { data, isLoading } = useSWR<{ total: number; items: Signal[] }>(`/api/signals?${qs}`, { refreshInterval: 30_000 });
  const { data: top } = useSWR<{ items: Signal[] }>("/api/signals/top?limit=8", { refreshInterval: 30_000 });

  return (
    <div>
      <PageHeader
        title="AI Signals"
        subtitle="Setups produced by the rule-based, multi-timeframe engine. Each one lists the computed evidence, the levels, and what would invalidate it."
      />
      <Card className="mb-5">
        <CardHeader title="Open setups" subtitle="Not yet expired, awaiting entry or in progress" />
        <div className="p-3">
          {!top && <Skeleton className="h-40" />}
          {top && top.items.length === 0 && (
            <Empty
              icon={<Radar className="h-6 w-6" />}
              title="WAIT — No high-quality setup detected"
              body="The engine only issues BUY or SELL when trend, structure, momentum, levels and higher timeframes agree and reward/risk clears the minimum."
            />
          )}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">{top?.items.map((s) => <SetupCard key={s.id} s={s} />)}</div>
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Signal history"
          subtitle={data ? `${data.total.toLocaleString()} evaluations` : undefined}
          action={
            <Segmented
              size="sm"
              value={status}
              onChange={(v) => {
                setStatus(v);
                setPage(0);
              }}
              options={[
                { value: "ACTIONABLE", label: "BUY / SELL" },
                { value: "WAIT", label: "WAIT" },
                { value: "ALL", label: "All" },
              ]}
            />
          }
        />
        <div className="grid grid-cols-2 gap-2 border-b border-line p-3 sm:grid-cols-4">
          <Select value={asset} onChange={(e) => (setAsset(e.target.value), setPage(0))} aria-label="Market filter">
            <option value="">All markets</option>
            {mk?.markets.map((m) => (
              <option key={m.id} value={m.id}>
                {m.symbol}
              </option>
            ))}
          </Select>
          <Select value={tf} onChange={(e) => (setTf(e.target.value), setPage(0))} aria-label="Timeframe filter">
            <option value="">All timeframes</option>
            {["15m", "1h", "4h"].map((t) => (
              <option key={t}>{t}</option>
            ))}
          </Select>
          <Select value={dir} onChange={(e) => (setDir(e.target.value), setPage(0))} aria-label="Direction filter">
            <option value="">All directions</option>
            <option value="BUY">BUY</option>
            <option value="SELL">SELL</option>
          </Select>
        </div>
        {isLoading && <Skeleton className="m-4 h-64" />}
        {data && data.items.length === 0 && <Empty title="No signals match these filters" body="The engine writes an evaluation for every closed candle it analyses." />}
        {data && data.items.length > 0 && <SignalTable items={data.items} />}
        {data && data.total > PAGE && (
          <div className="flex items-center justify-between border-t border-line px-4 py-2.5 text-xs text-muted">
            <span>
              Page {page + 1} of {Math.ceil(data.total / PAGE)}
            </span>
            <div className="flex gap-2">
              <Button size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
                Previous
              </Button>
              <Button size="sm" disabled={(page + 1) * PAGE >= data.total} onClick={() => setPage(page + 1)}>
                Next
              </Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
