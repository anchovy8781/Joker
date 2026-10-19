"use client";

import { useEffect, useMemo, useState } from "react";
import { api, ApiError, newIdempotencyKey, type Asset, type Order, type Trade } from "@/lib/api";
import { buzz, toast } from "@/lib/feedback";
import { credits, estimateFee, orderStatusLabel, reasonLabel } from "@/lib/format";
import { roundTick, stepTick, tickSize } from "@/lib/krx";

type Result = { order: Order; fills: Trade[]; replayed: boolean };

export function OrderPanel({ asset, availableCash, availableShares, pickedPrice, onDone, loggedIn, marketOpen = true, requestedSide }: {
  asset: Asset;
  availableCash: number | null;
  availableShares: number | null;
  pickedPrice?: number | null;
  onDone?: () => void;
  loggedIn: boolean;
  marketOpen?: boolean;
  requestedSide?: { side: "buy" | "sell"; n: number };
}) {
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [type, setType] = useState<"limit" | "market">("limit");
  const [qty, setQty] = useState("1");
  const [price, setPrice] = useState(String(asset.current_price || asset.reference_price));
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  // One key per intended order: a retry after a network error reuses it, so the server
  // can never create a duplicate order. Any edit to the form starts a new intent.
  const [key, setKey] = useState(newIdempotencyKey);

  useEffect(() => { if (pickedPrice) { setPrice(String(pickedPrice)); setType("limit"); } }, [pickedPrice]);
  useEffect(() => { if (requestedSide) setSide(requestedSide.side); }, [requestedSide]);
  useEffect(() => { setKey(newIdempotencyKey()); }, [side, type, qty, price, asset.id]);

  const q = Number.parseInt(qty, 10);
  const p = Number.parseInt(price, 10);
  const valid = Number.isInteger(q) && q > 0 && (type === "market" || (Number.isInteger(p) && p > 0));
  const est = useMemo(() => {
    if (!valid) return null;
    const unit = type === "limit" ? p : asset.current_price;
    const notional = q * unit;
    return { notional, fee: estimateFee(notional, asset.fee_bps) };
  }, [valid, q, p, type, asset.current_price, asset.fee_bps]);
  const surcharge = asset.fee_bps > 20;
  // Largest quantity the account can afford (buy) or holds (sell), for the quick-fill buttons.
  const unitPrice = type === "limit" && Number.isInteger(p) && p > 0 ? p : asset.current_price;
  const maxQty = side === "buy"
    ? Math.floor((availableCash ?? 0) / (unitPrice + Math.ceil((unitPrice * asset.fee_bps) / 10000)))
    : availableShares ?? 0;
  const fill = (fraction: number) => setQty(String(Math.max(fraction === 1 ? maxQty : 1, Math.floor(maxQty * fraction))));
  // Prices move on the 호가단위 grid, never past 상한가/하한가.
  const clamp = (v: number) => Math.min(asset.upper_limit || v, Math.max(asset.lower_limit || 1, v));
  const nudge = (dir: 1 | -1) => setPrice(String(clamp(stepTick(roundTick(Number.isInteger(p) && p > 0 ? p : asset.current_price), dir))));
  const offTick = type === "limit" && Number.isInteger(p) && p > 0 && p % tickSize(p) !== 0;
  const inVi = !!asset.vi_until && new Date(asset.vi_until).getTime() > Date.now();

  const halted = asset.trading_status !== "active";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!valid || pending) return;
    setPending(true);
    setError(null);
    setResult(null);
    try {
      const body: Record<string, unknown> = { asset_id: asset.id, side, order_type: type, quantity: q, idempotency_key: key };
      if (type === "limit") body.limit_price = p;
      const r = await api<Result>("/api/orders", { method: "POST", body });
      setResult(r); // shown only after the server has validated and executed the order
      setKey(newIdempotencyKey());
      const filled = r.fills.reduce((n, f) => n + f.quantity, 0);
      if (filled > 0) {
        const avg = Math.round(r.fills.reduce((v, f) => v + f.execution_price * f.quantity, 0) / filled);
        toast(`${asset.symbol} ${filled}주 ${side === "buy" ? "매수" : "매도"} 체결 · 평균 ₩${credits(avg)}`, side === "buy" ? "up" : "down");
        buzz([20, 40, 20]);
      } else if (r.order.status === "rejected" || r.order.status === "canceled") {
        toast(`주문이 체결되지 않았어요${r.order.status_reason ? ` · ${reasonLabel(r.order.status_reason)}` : ""}`, "warn");
        buzz(60);
      } else {
        toast(`${asset.symbol} ${side === "buy" ? "매수" : "매도"} 주문 접수 · 가격이 닿으면 체결돼요`);
        buzz(15);
      }
      onDone?.();
    } catch (err) {
      buzz(60);
      setError(err instanceof ApiError ? `${err.message}${err.code === "PRICE_OUT_OF_BAND" ? ` (${credits(err.details.min as number)} ~ ${credits(err.details.max as number)})` : ""}` : "네트워크 오류: 다시 시도하면 같은 주문으로 안전하게 재전송됩니다.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3" aria-label="주문 패널">
      <div className="grid grid-cols-2 gap-1 rounded-full bg-panel2 p-1">
        {(["buy", "sell"] as const).map((s) => (
          <button key={s} type="button" onClick={() => setSide(s)} aria-pressed={side === s}
            className={`rounded-full py-2 text-sm font-semibold ${side === s ? (s === "buy" ? "bg-up text-white" : "bg-down text-white") : "text-muted"}`}>
            {s === "buy" ? "▲ 매수" : "▼ 매도"}
          </button>
        ))}
      </div>
      <div className="flex gap-2 text-sm">
        {(["limit", "market"] as const).map((t) => (
          <label key={t} className={`flex-1 cursor-pointer rounded-full border px-3 py-1.5 text-center ${type === t ? "border-text text-text" : "border-line text-muted"}`}>
            <input type="radio" className="sr-only" name="otype" checked={type === t} onChange={() => setType(t)} />
            {t === "limit" ? "지정가" : "시장가"}
          </label>
        ))}
      </div>
      {type === "limit" && (
        <label className="block text-xs text-muted">가격 (크레딧)
          <input className="input mt-1" inputMode="numeric" value={price} onChange={(e) => setPrice(e.target.value.replace(/\D/g, ""))}
            onBlur={() => { if (offTick) setPrice(String(clamp(roundTick(p)))); }} aria-label="지정가" />
          <span className="mt-1.5 grid grid-cols-5 gap-1">
            {asset.lower_limit > 0 && <button type="button" className="chip px-1 py-1.5 text-[11px] text-down" onClick={() => setPrice(String(asset.lower_limit))}>하한가</button>}
            <button type="button" className="chip px-1 py-1.5 text-xs" onClick={() => nudge(-1)} aria-label="한 호가 내리기">−호가</button>
            <button type="button" className="chip px-1 py-1.5 text-xs" onClick={() => setPrice(String(asset.current_price))}>현재가</button>
            <button type="button" className="chip px-1 py-1.5 text-xs" onClick={() => nudge(1)} aria-label="한 호가 올리기">+호가</button>
            {asset.upper_limit > 0 && <button type="button" className="chip px-1 py-1.5 text-[11px] text-up" onClick={() => setPrice(String(asset.upper_limit))}>상한가</button>}
          </span>
          <span className="mt-1 block">
            호가단위 {credits(tickSize(Number.isInteger(p) && p > 0 ? p : asset.current_price))}원
            {asset.lower_limit > 0 && <> · 가격범위 {credits(asset.lower_limit)} ~ {credits(asset.upper_limit)}</>}
            {offTick && <span className="text-amber-500"> · 호가단위에 맞지 않아요</span>}
          </span>
        </label>
      )}
      <label className="block text-xs text-muted">수량 (주)
        <input className="input mt-1" inputMode="numeric" value={qty} onChange={(e) => setQty(e.target.value.replace(/\D/g, ""))} aria-label="수량" />
        {loggedIn && maxQty > 0 && (
          <span className="mt-1.5 flex gap-1.5">
            {([[0.1, "10%"], [0.25, "25%"], [0.5, "50%"], [1, "최대"]] as const).map(([f, l]) => (
              <button key={l} type="button" className="chip flex-1 px-2 py-1.5 text-xs" onClick={() => fill(f)}>{l}</button>
            ))}
          </span>
        )}
      </label>
      <div className="space-y-1 rounded-xl bg-panel2 p-3 text-xs text-muted">
        <div className="flex justify-between"><span>{side === "buy" ? "주문 가능 현금" : "매도 가능 수량"}</span>
          <span className="font-mono text-text">{side === "buy" ? credits(availableCash) : credits(availableShares)}</span></div>
        {est && <>
          <div className="flex justify-between"><span>예상 체결 금액{type === "market" && " (현재가 기준)"}</span><span className="font-mono text-text">{credits(est.notional)}</span></div>
          <div className="flex justify-between"><span>예상 수수료 ({asset.fee_bps / 100}%{surcharge && " 할증"})</span><span className="font-mono text-text">{credits(est.fee)}</span></div>
        </>}
        <p className="pt-1 text-xs">실제 체결가와 수수료는 서버가 계산합니다. {type === "market" && "시장가는 기준가 ±5% 범위에서만 체결되고 잔량은 취소됩니다."}
          {surcharge && side === "sell" && " 할증 종목을 손실 매도하면 매도 수수료가 면제됩니다."}</p>
        {inVi && <p className="pt-1 text-xs text-amber-500">⚡ 변동성완화장치(VI) 발동 — 2분간 주문을 모아 한 가격(단일가)으로 체결합니다.</p>}
        {!marketOpen && <p className="pt-1 text-xs text-amber-300">◐ 장마감 중 — 예약 주문으로 접수되어 다음 장 시작 시 접수 순서대로 제출됩니다.</p>}
      </div>
      {!loggedIn ? (
        <a href="/login" className="btn-primary block w-full text-center">로그인 후 거래</a>
      ) : (
        <button type="submit" disabled={!valid || pending || halted}
          className={`w-full rounded-full py-3 text-base font-bold disabled:opacity-40 ${side === "buy" ? "bg-up text-white" : "bg-down text-white"}`}>
          {halted ? "⏸ 거래 정지 중" : pending ? "서버 처리 중…" : `${side === "buy" ? "매수" : "매도"} ${marketOpen ? "주문" : "예약"}`}
        </button>
      )}
      {error && <div role="alert" className="rounded-full border border-red-500/50 bg-red-500/10 p-2 text-xs text-red-200">⚠ {error}</div>}
      {result && (
        <div role="status" className="rounded-full border border-line bg-panel2 p-2 text-xs">
          <div className="font-semibold text-text">
            {result.order.status === "rejected" ? "✕" : "✓"} 주문 #{result.order.id} · {orderStatusLabel(result.order.status)}
            {result.replayed && " (중복 요청 — 기존 주문 반환)"}
          </div>
          <div className="mt-1 text-muted">체결 {credits(result.order.filled_quantity)} / {credits(result.order.quantity)}주
            {result.fills.length > 0 && ` · 평균 ${credits(result.fills.reduce((s, f) => s + f.execution_price * f.quantity, 0) / result.fills.reduce((s, f) => s + f.quantity, 0))}`}
          </div>
          {result.order.status_reason && <div className="mt-1 text-amber-300">{reasonLabel(result.order.status_reason)}</div>}
        </div>
      )}
    </form>
  );
}
