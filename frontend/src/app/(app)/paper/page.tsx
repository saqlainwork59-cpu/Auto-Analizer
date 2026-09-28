"use client";

import clsx from "clsx";
import Link from "next/link";
import { useState } from "react";
import useSWR, { mutate } from "swr";
import { useLivePrice, useMarkets } from "@/components/MarketBits";
import {
  Button,
  Card,
  CardHeader,
  Delta,
  Empty,
  ErrorNote,
  Field,
  Input,
  Notice,
  PageHeader,
  Segmented,
  Select,
  Skeleton,
  Stat,
  TableWrap,
  td,
  th,
} from "@/components/ui";
import { api } from "@/lib/api";
import { dateTime, money, num, pct, price } from "@/lib/format";
import type { PaperTrade } from "@/lib/types";

interface Account {
  account: { starting_balance: number; cash_balance: number; realized_pnl: number; fees_paid: number; max_drawdown_pct: number; fee_rate: number };
  equity: number | null;
  equity_partial: number;
  priced_all: boolean;
  unrealized_pnl: number;
  current_drawdown_pct: number | null;
  positions: PaperTrade[];
  stats: { closed_trades: number; wins: number; losses: number; win_rate: number | null; profit_factor: number | null; longest_losing_streak: number; signal_linked_trades: number; signal_linked_pnl: number; manual_trades_pnl: number };
  enabled: boolean;
  disclaimer: string;
}

function Ticket() {
  const { data: mk } = useMarkets();
  const [assetId, setAssetId] = useState("");
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [mode, setMode] = useState<"qty" | "risk">("risk");
  const [qty, setQty] = useState("");
  const [risk, setRisk] = useState("1");
  const [sl, setSl] = useState("");
  const [tp, setTp] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const markets = (mk?.markets || []).filter((m) => m.data_status !== "unavailable");
  const m = markets.find((x) => String(x.id) === assetId) || markets[0];
  const px = useLivePrice(m);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!m) return;
    setBusy(true);
    setErr(null);
    try {
      await api("/api/paper/trades", {
        method: "POST",
        json: {
          asset_id: m.id,
          direction: side,
          quantity: mode === "qty" ? Number(qty) : null,
          risk_pct: mode === "risk" ? Number(risk) : null,
          stop_loss: sl ? Number(sl) : null,
          take_profit: tp ? Number(tp) : null,
        },
      });
      setQty("");
      setSl("");
      setTp("");
      mutate((k) => typeof k === "string" && k.startsWith("/api/paper"));
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  }
  if (mk && markets.length === 0)
    return <Empty title="No live prices available" body="Simulated trades need a verified live price. Connect a data provider to trade on paper." />;
  return (
    <form onSubmit={submit} className="space-y-3 p-4">
      <Field label="Market">
        <Select value={m ? String(m.id) : ""} onChange={(e) => setAssetId(e.target.value)}>
          {markets.map((x) => (
            <option key={x.id} value={x.id}>
              {x.symbol}
            </option>
          ))}
        </Select>
      </Field>
      <div className="flex items-center justify-between rounded-lg bg-surface-2 px-3 py-2 text-xs">
        <span className="text-muted">Live price</span>
        <span className="num font-medium">{px ? price(px.price, m?.price_precision) : "unavailable"}</span>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Button type="button" variant={side === "BUY" ? "buy" : "secondary"} onClick={() => setSide("BUY")}>
          Buy
        </Button>
        <Button type="button" variant={side === "SELL" ? "sell" : "secondary"} onClick={() => setSide("SELL")}>
          Sell
        </Button>
      </div>
      <Segmented value={mode} onChange={setMode} options={[{ value: "risk", label: "Size by risk %" }, { value: "qty", label: "Quantity" }]} />
      {mode === "qty" ? (
        <Field label="Quantity">
          <Input type="number" min={0} step="any" value={qty} onChange={(e) => setQty(e.target.value)} required />
        </Field>
      ) : (
        <Field label="Risk (% of equity)" hint="Requires a stop-loss">
          <Input type="number" min={0.1} max={5} step={0.1} value={risk} onChange={(e) => setRisk(e.target.value)} />
        </Field>
      )}
      <div className="grid grid-cols-2 gap-2">
        <Field label="Stop-loss">
          <Input type="number" step="any" value={sl} onChange={(e) => setSl(e.target.value)} required={mode === "risk"} />
        </Field>
        <Field label="Take-profit">
          <Input type="number" step="any" value={tp} onChange={(e) => setTp(e.target.value)} />
        </Field>
      </div>
      <ErrorNote error={err} />
      <Button type="submit" variant="primary" className="w-full" loading={busy} disabled={!px}>
        Open simulated {side === "BUY" ? "long" : "short"}
      </Button>
    </form>
  );
}

function Reset() {
  const [bal, setBal] = useState("10000");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  async function go() {
    if (!confirm("Reset the paper account? Open simulated positions are closed without P&L.")) return;
    setBusy(true);
    try {
      await api("/api/paper/reset", { method: "POST", json: { balance: Number(bal) } });
      mutate((k) => typeof k === "string" && k.startsWith("/api/paper"));
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-2 p-4">
      <div className="flex items-end gap-2">
        <Field label="Virtual balance ($)" className="flex-1">
          <Input type="number" min={100} max={100000000} value={bal} onChange={(e) => setBal(e.target.value)} />
        </Field>
        <Button onClick={go} loading={busy}>
          Reset
        </Button>
      </div>
      <ErrorNote error={err} />
    </div>
  );
}

export default function PaperPage() {
  const { data: a } = useSWR<Account>("/api/paper", { refreshInterval: 5000 });
  const { data: hist } = useSWR<PaperTrade[]>("/api/paper/trades?status=CLOSED&limit=200", { refreshInterval: 15000 });
  const [closing, setClosing] = useState<number | null>(null);
  const [err, setErr] = useState<unknown>(null);
  async function close(id: number) {
    setClosing(id);
    setErr(null);
    try {
      await api(`/api/paper/trades/${id}/close`, { method: "POST" });
      mutate((k) => typeof k === "string" && k.startsWith("/api/paper"));
    } catch (e) {
      setErr(e);
    } finally {
      setClosing(null);
    }
  }
  const equity = a ? a.equity ?? a.equity_partial : null;
  const total = a && equity != null ? equity - a.account.starting_balance : null;
  return (
    <div>
      <PageHeader title="Paper Trading" subtitle="Practise on live prices with virtual money. Completely separate from any real-money account." />
      {a && !a.enabled && (
        <div className="mb-4">
          <Notice tone="wait">Paper trading is temporarily disabled by an administrator. Existing positions keep being tracked.</Notice>
        </div>
      )}
      <Card className="mb-5">
        {!a ? (
          <Skeleton className="m-4 h-20" />
        ) : (
          <div className="grid grid-cols-2 gap-5 p-5 sm:grid-cols-3 lg:grid-cols-6">
            <Stat label="Equity" value={a.equity == null ? "—" : money(a.equity)} sub={a.priced_all ? "live marked" : "some prices unavailable"} />
            <Stat label="Total P&L" value={<Delta v={total}>{money(total, true)}</Delta>} sub={total != null ? pct((total / a.account.starting_balance) * 100) : undefined} />
            <Stat label="Unrealised" value={<Delta v={a.unrealized_pnl}>{money(a.unrealized_pnl, true)}</Delta>} />
            <Stat label="Realised" value={<Delta v={a.account.realized_pnl}>{money(a.account.realized_pnl, true)}</Delta>} sub={`fees ${money(a.account.fees_paid)}`} />
            <Stat label="Drawdown" value={a.current_drawdown_pct == null ? "—" : pct(a.current_drawdown_pct, 2, false)} sub={`max ${pct(a.account.max_drawdown_pct, 2, false)}`} />
            <Stat
              label="Win rate"
              value={a.stats.win_rate == null ? "—" : pct(a.stats.win_rate * 100, 0, false)}
              sub={`${a.stats.closed_trades} closed · PF ${a.stats.profit_factor == null ? "—" : num(a.stats.profit_factor, 2)}`}
            />
          </div>
        )}
      </Card>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 space-y-5">
          <Card>
            <CardHeader title="Open positions" subtitle="Marked to the latest verified feed price; stops and targets are checked on every update" />
            <ErrorNote error={err} />
            {a && a.positions.length === 0 ? (
              <Empty title="No open simulated positions" body="Open one from the ticket, or from any actionable signal's detail page." />
            ) : (
              <TableWrap>
                <table className="w-full min-w-[760px]">
                  <thead className="border-b border-line">
                    <tr>
                      {["Market", "Side", "Qty", "Entry", "Price", "Stop", "Target", "Unrealised", ""].map((h) => (
                        <th key={h} className={th}>
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {a?.positions.map((t) => (
                      <tr key={t.id}>
                        <td className={td}>
                          <span className="font-medium">{t.asset.symbol}</span>
                          {t.signal_id && (
                            <Link href={`/signals/${t.signal_id}`} className="ml-1.5 text-[11px] text-accent hover:underline">
                              signal #{t.signal_id}
                            </Link>
                          )}
                        </td>
                        <td className={clsx(td, "text-xs font-semibold", t.direction === "BUY" ? "text-buy" : "text-sell")}>{t.direction}</td>
                        <td className={clsx(td, "num text-xs")}>{num(t.quantity, 6)}</td>
                        <td className={clsx(td, "num text-xs")}>{price(t.entry_price, t.asset.price_precision)}</td>
                        <td className={clsx(td, "num text-xs")}>{t.current_price == null ? "unavailable" : price(t.current_price, t.asset.price_precision)}</td>
                        <td className={clsx(td, "num text-xs text-sell")}>{price(t.stop_loss, t.asset.price_precision)}</td>
                        <td className={clsx(td, "num text-xs text-buy")}>{price(t.take_profit, t.asset.price_precision)}</td>
                        <td className={clsx(td, "num text-xs")}>
                          <Delta v={t.unrealized_pnl}>{money(t.unrealized_pnl, true)}</Delta>
                        </td>
                        <td className={td}>
                          <Button size="sm" onClick={() => close(t.id)} loading={closing === t.id}>
                            Close
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
          </Card>

          <Card>
            <CardHeader
              title="Trade history"
              subtitle={a ? `Signal-linked P&L ${money(a.stats.signal_linked_pnl, true)} (${a.stats.signal_linked_trades} trades) · manual ${money(a.stats.manual_trades_pnl, true)}` : undefined}
            />
            {hist && hist.length === 0 ? (
              <Empty title="No closed trades yet" />
            ) : (
              <TableWrap>
                <table className="w-full min-w-[760px]">
                  <thead className="border-b border-line">
                    <tr>
                      {["Closed", "Market", "Side", "Entry", "Exit", "Reason", "Fees", "P&L", "Signal"].map((h) => (
                        <th key={h} className={th}>
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {(hist || []).map((t) => (
                      <tr key={t.id}>
                        <td className={clsx(td, "text-xs text-muted")}>{dateTime(t.closed_at)}</td>
                        <td className={clsx(td, "text-xs font-medium")}>{t.asset.symbol}</td>
                        <td className={clsx(td, "text-xs font-semibold", t.direction === "BUY" ? "text-buy" : "text-sell")}>{t.direction}</td>
                        <td className={clsx(td, "num text-xs")}>{price(t.entry_price, t.asset.price_precision)}</td>
                        <td className={clsx(td, "num text-xs")}>{price(t.exit_price, t.asset.price_precision)}</td>
                        <td className={clsx(td, "text-xs")}>{t.close_reason}</td>
                        <td className={clsx(td, "num text-xs text-muted")}>{money(t.fees)}</td>
                        <td className={clsx(td, "num text-xs")}>
                          <Delta v={t.pnl}>{money(t.pnl, true)}</Delta>
                        </td>
                        <td className={clsx(td, "text-xs")}>
                          {t.signal_id ? (
                            <Link href={`/signals/${t.signal_id}`} className="text-accent hover:underline">
                              #{t.signal_id}
                            </Link>
                          ) : (
                            <span className="text-subtle">manual</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
          </Card>
        </div>
        <div className="space-y-5">
          <Card>
            <CardHeader title="Trade ticket" subtitle={a ? `Fee ${(a.account.fee_rate * 100).toFixed(2)}% per side` : undefined} />
            <Ticket />
          </Card>
          <Card>
            <CardHeader title="Account" />
            <Reset />
          </Card>
          {a && <p className="px-1 text-[11px] leading-relaxed text-subtle">{a.disclaimer}</p>}
        </div>
      </div>
    </div>
  );
}
