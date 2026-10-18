"use client";

import { useState } from "react";
import { api, ApiError, newIdempotencyKey, type Order } from "@/lib/api";
import { buzz, toast } from "@/lib/feedback";
import { credits, orderStatusLabel } from "@/lib/format";

const ACTIVE = ["open", "partially_filled", "scheduled"];

/** My orders on one stock, with 정정 (change price/quantity) and 취소 right in the row. */
export function MyOrders({ orders, currentPrice, onChange }: { orders: Order[]; currentPrice: number; onChange: () => void }) {
  const [editing, setEditing] = useState<number | null>(null);
  const [price, setPrice] = useState("");
  const [qty, setQty] = useState("");
  const [busy, setBusy] = useState(false);

  const open = (o: Order) => {
    setEditing(o.id);
    setPrice(String(o.limit_price ?? currentPrice));
    setQty(String(o.quantity - o.filled_quantity));
  };
  const run = async (fn: () => Promise<unknown>, done: string) => {
    setBusy(true);
    try { await fn(); toast(done); buzz(15); setEditing(null); onChange(); }
    catch (e) { toast(e instanceof ApiError ? e.message : "처리하지 못했어요", "warn"); buzz(60); }
    finally { setBusy(false); }
  };
  const step = (bps: number) => setPrice(String(Math.max(1, Math.round((Number(price) || currentPrice) * (1 + bps / 10000)))));

  if (!orders.length) return <div className="py-6 text-center text-sm text-muted">주문 없음</div>;
  return (
    <ul className="space-y-2 text-sm">
      {orders.map((o) => {
        const active = ACTIVE.includes(o.status);
        return (
          <li key={o.id} className="rounded-2xl bg-panel2 px-3 py-2.5">
            <div className="flex items-center gap-2">
              <span className={`font-semibold ${o.side === "buy" ? "text-up" : "text-down"}`}>{o.side === "buy" ? "매수" : "매도"}</span>
              <span className="tabular-nums">{o.order_type === "limit" ? `₩${credits(o.limit_price)}` : "시장가"}</span>
              <span className="ml-auto text-xs text-muted tabular-nums">{o.filled_quantity}/{o.quantity}주 · {orderStatusLabel(o.status)}</span>
            </div>
            {active && editing !== o.id && (
              <div className="mt-2 flex gap-1.5">
                {o.order_type === "limit" && o.status !== "scheduled" && (
                  <button className="btn-ghost flex-1 !bg-panel py-1.5 text-xs" disabled={busy} onClick={() => open(o)}>정정</button>
                )}
                <button className="btn-ghost flex-1 !bg-panel py-1.5 text-xs" disabled={busy}
                  onClick={() => run(() => api(`/api/orders/${o.id}/cancel`, { method: "POST" }), "주문을 취소했어요")}>취소</button>
              </div>
            )}
            {editing === o.id && (
              <div className="mt-2 space-y-2">
                <div className="grid grid-cols-2 gap-2">
                  <label className="text-xs text-muted">가격
                    <input className="input mt-1 !bg-panel py-2 text-sm" inputMode="numeric" value={price} onChange={(e) => setPrice(e.target.value.replace(/\D/g, ""))} />
                  </label>
                  <label className="text-xs text-muted">수량 (남은 수량)
                    <input className="input mt-1 !bg-panel py-2 text-sm" inputMode="numeric" value={qty} onChange={(e) => setQty(e.target.value.replace(/\D/g, ""))} />
                  </label>
                </div>
                <div className="flex gap-1.5">
                  <button type="button" className="chip flex-1 !bg-panel px-2 py-1.5 text-xs" onClick={() => step(-50)}>−0.5%</button>
                  <button type="button" className="chip flex-1 !bg-panel px-2 py-1.5 text-xs" onClick={() => setPrice(String(currentPrice))}>현재가</button>
                  <button type="button" className="chip flex-1 !bg-panel px-2 py-1.5 text-xs" onClick={() => step(50)}>+0.5%</button>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <button className="btn-primary py-2" disabled={busy || !(Number(price) > 0) || !(Number(qty) > 0)}
                    onClick={() => run(() => api(`/api/orders/${o.id}/amend`, { method: "POST",
                      body: { limit_price: Number(price), quantity: Number(qty), idempotency_key: newIdempotencyKey() } }),
                      `정정 완료 · ₩${credits(Number(price))} ${qty}주`)}>
                    {busy ? "처리 중…" : "정정하기"}
                  </button>
                  <button className="btn-ghost !bg-panel py-2" disabled={busy} onClick={() => setEditing(null)}>닫기</button>
                </div>
                <p className="text-xs text-muted">정정하면 남은 수량이 새 가격으로 다시 걸려요(대기 순서는 맨 뒤). 바로 체결될 가격이면 즉시 체결돼요.</p>
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
