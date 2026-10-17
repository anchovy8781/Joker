import type { OrderBook } from "@/lib/api";
import { credits } from "@/lib/format";
import { Empty } from "./ui";

export function OrderBookView({ book, onPick }: { book: OrderBook | null; onPick?: (price: number) => void }) {
  if (!book) return <Empty>불러오는 중…</Empty>;
  const asks = [...book.asks].reverse();
  const max = Math.max(1, ...book.asks.map((l) => l.quantity), ...book.bids.map((l) => l.quantity));
  const Row = ({ price, qty, side }: { price: number; qty: number; side: "ask" | "bid" }) => (
    <button type="button" onClick={() => onPick?.(price)}
      className="relative grid w-full grid-cols-2 px-2 py-1 text-left font-mono text-xs tabular-nums hover:bg-panel2">
      <span className={`absolute inset-y-0 right-0 ${side === "ask" ? "bg-down/10" : "bg-up/10"}`} style={{ width: `${(qty / max) * 100}%` }} aria-hidden />
      <span className={`relative ${side === "ask" ? "text-down" : "text-up"}`}>{side === "ask" ? "매도" : "매수"} {credits(price)}</span>
      <span className="relative text-right text-text">{credits(qty)}</span>
    </button>
  );
  return (
    <div>
      <div className="grid grid-cols-2 px-2 pb-1 text-[11px] text-muted"><span>호가</span><span className="text-right">잔량</span></div>
      {asks.length === 0 ? <Empty>매도 호가 없음</Empty> : asks.map((l) => <Row key={`a${l.price}`} price={l.price} qty={l.quantity} side="ask" />)}
      <div className="my-1 border-t border-line" />
      {book.bids.length === 0 ? <Empty>매수 호가 없음</Empty> : book.bids.map((l) => <Row key={`b${l.price}`} price={l.price} qty={l.quantity} side="bid" />)}
    </div>
  );
}
