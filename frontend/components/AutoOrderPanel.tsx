"use client";

import { useState } from "react";
import { api, ApiError, type Asset, type AutoOrder } from "@/lib/api";
import { credits, dateTime } from "@/lib/format";
import { useApi } from "@/lib/hooks";

const STATUS: Record<string, string> = { active: "감시 중", triggered: "실행됨", canceled: "취소", failed: "실패", expired: "만료" };

/** 지정가 자동 매수/매도: submit an order automatically when the price reaches a trigger. */
export function AutoOrderPanel({ asset }: { asset: Asset }) {
  const list = useApi<AutoOrder[]>(`/api/auto-orders?asset_id=${asset.id}`, 10_000);
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [trigger, setTrigger] = useState(String(Math.round(asset.current_price * 0.97)));
  const [orderType, setOrderType] = useState<"limit" | "market">("limit");
  const [limit, setLimit] = useState(String(Math.round(asset.current_price * 0.97)));
  const [qty, setQty] = useState("1");
  const [days, setDays] = useState("7");
  const [msg, setMsg] = useState<string | null>(null);
  // Buy when it falls to X (<=), sell when it rises to X (>=) — the usual target-price setup.
  const condition = side === "buy" ? "lte" : "gte";

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setMsg(null);
    try {
      const body: Record<string, unknown> = { asset_id: asset.id, side, trigger_condition: condition,
        trigger_price: Number(trigger), order_type: orderType, quantity: Number(qty), days: Number(days) };
      if (orderType === "limit") body.limit_price = Number(limit);
      const r = await api<AutoOrder>("/api/auto-orders", { method: "POST", body });
      setMsg(r.status === "triggered" ? "✓ 조건이 이미 충족되어 바로 주문했습니다" : r.status === "failed" ? `⚠ ${r.failure_reason}` : "✓ 자동 주문이 등록되었습니다");
      list.reload();
    } catch (err) {
      setMsg(err instanceof ApiError ? `⚠ ${err.message}` : "실패");
    }
  }

  return (
    <div className="space-y-3">
      <form onSubmit={create} className="space-y-2 text-sm">
        <div className="grid grid-cols-2 gap-1 rounded-full bg-panel2 p-1">
          {(["buy", "sell"] as const).map((s) => (
            <button type="button" key={s} onClick={() => setSide(s)} aria-pressed={side === s}
              className={`rounded-full py-1.5 text-xs font-semibold ${side === s ? (s === "buy" ? "bg-up text-white" : "bg-down text-white") : "text-muted"}`}>
              {s === "buy" ? "▲ 자동 매수" : "▼ 자동 매도"}
            </button>
          ))}
        </div>
        <label className="block text-xs text-muted">
          현재가가 <b className="text-text">{side === "buy" ? "이하로 내려가면" : "이상으로 오르면"}</b> (감시 가격)
          <input className="input mt-1" inputMode="numeric" value={trigger} onChange={(e) => setTrigger(e.target.value.replace(/\D/g, ""))} aria-label="감시 가격" />
        </label>
        <div className="flex gap-2">
          {(["limit", "market"] as const).map((t) => (
            <button type="button" key={t} onClick={() => setOrderType(t)}
              className={`flex-1 rounded-full border px-2 py-1 text-xs ${orderType === t ? "border-text" : "border-line text-muted"}`}>
              {t === "limit" ? "지정가로 주문" : "시장가로 주문"}
            </button>
          ))}
        </div>
        {orderType === "limit" && (
          <label className="block text-xs text-muted">주문 가격
            <input className="input mt-1" inputMode="numeric" value={limit} onChange={(e) => setLimit(e.target.value.replace(/\D/g, ""))} aria-label="자동 주문 가격" />
          </label>
        )}
        <div className="grid grid-cols-2 gap-2">
          <label className="block text-xs text-muted">수량
            <input className="input mt-1" inputMode="numeric" value={qty} onChange={(e) => setQty(e.target.value.replace(/\D/g, ""))} aria-label="자동 주문 수량" />
          </label>
          <label className="block text-xs text-muted">유효 기간
            <select className="input mt-1" value={days} onChange={(e) => setDays(e.target.value)}>
              {[1, 3, 7, 14, 30].map((d) => <option key={d} value={d}>{d}일</option>)}
            </select>
          </label>
        </div>
        <button className="btn-primary w-full py-2" disabled={!trigger || !qty}>자동 주문 등록</button>
        {msg && <p role="status" className="text-xs">{msg}</p>}
        <p className="text-xs text-muted">조건이 충족되는 순간 일반 주문과 같은 규칙으로 제출됩니다. 그때 잔액·보유 수량이 부족하면 실패로 처리되고 알림이 옵니다. 장외 시간에 충족되면 예약 주문이 됩니다.</p>
      </form>
      {!!list.data?.length && (
        <ul className="space-y-1.5 text-xs">
          {list.data.slice(0, 10).map((a) => (
            <li key={a.id} className="flex items-center justify-between gap-2 rounded bg-panel2 px-2 py-1.5">
              <span className={a.side === "buy" ? "text-up" : "text-down"}>
                {a.side === "buy" ? "▲" : "▼"} {a.trigger_condition === "lte" ? "≤" : "≥"} {credits(a.trigger_price)} → {a.order_type === "limit" ? credits(a.limit_price) : "시장가"} × {a.quantity}
              </span>
              <span className="text-muted" title={a.failure_reason ?? `만료 ${dateTime(a.expires_at)}`}>{STATUS[a.status] ?? a.status}</span>
              {a.status === "active" && (
                <button className="btn-ghost px-2 py-0.5 text-xs" onClick={async () => { await api(`/api/auto-orders/${a.id}/cancel`, { method: "POST" }); list.reload(); }}>취소</button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
