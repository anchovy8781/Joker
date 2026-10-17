import type { OrderBook } from "@/lib/api";
import { credits } from "@/lib/format";
import { Empty } from "./ui";

/** 호가창: sell orders on top (remaining qty on the left), buy orders below (qty on the
 *  right), prices in the middle coloured against the previous close. Tap a price to use it. */
export function OrderBookView({ book, onPick, currentPrice, previousPrice }: {
  book: OrderBook | null; onPick?: (price: number) => void; currentPrice?: number; previousPrice?: number;
}) {
  if (!book) return <Empty>불러오는 중…</Empty>;
  const asks = [...book.asks].reverse();
  const max = Math.max(1, ...book.asks.map((l) => l.quantity), ...book.bids.map((l) => l.quantity));
  const totalAsk = book.asks.reduce((s, l) => s + l.quantity, 0);
  const totalBid = book.bids.reduce((s, l) => s + l.quantity, 0);
  const tone = (p: number) => (!previousPrice || p === previousPrice ? "text-text" : p > previousPrice ? "text-up" : "text-down");
  const pct = (p: number) => (previousPrice ? ((p - previousPrice) / previousPrice) * 100 : 0);

  const Row = ({ price, qty, side }: { price: number; qty: number; side: "ask" | "bid" }) => {
    const w = `${Math.max(4, (qty / max) * 100)}%`;
    const current = price === currentPrice;
    return (
      <button type="button" onClick={() => onPick?.(price)} aria-label={`${side === "ask" ? "매도" : "매수"} 호가 ${credits(price)}, 잔량 ${credits(qty)}`}
        className={`grid w-full grid-cols-[1fr_auto_1fr] items-center gap-1 text-sm tabular-nums hover:bg-panel2/60 ${side === "ask" ? "bg-down/[0.06]" : "bg-up/[0.06]"}`}>
        <span className="relative flex h-9 items-center justify-end pr-2">
          {side === "ask" && <>
            <span className="absolute inset-y-1.5 right-0 rounded-full bg-down/25" style={{ width: w }} aria-hidden />
            <span className="relative">{credits(qty)}</span>
          </>}
        </span>
        <span className={`min-w-[7.5rem] rounded-full px-2 py-1 text-center ${current ? "ring-1 ring-text" : ""}`}>
          <b className={tone(price)}>{credits(price)}</b>
          <span className={`ml-1 text-xs ${tone(price)}`}>{pct(price) >= 0 ? "+" : ""}{pct(price).toFixed(2)}%</span>
        </span>
        <span className="relative flex h-9 items-center pl-2">
          {side === "bid" && <>
            <span className="absolute inset-y-1.5 left-0 rounded-full bg-up/25" style={{ width: w }} aria-hidden />
            <span className="relative">{credits(qty)}</span>
          </>}
        </span>
      </button>
    );
  };

  return (
    <div className="overflow-hidden rounded-xl">
      <div className="grid grid-cols-[1fr_auto_1fr] gap-1 pb-1.5 text-xs text-muted">
        <span className="pr-2 text-right">매도 잔량</span><span className="min-w-[7.5rem] text-center">호가</span><span className="pl-2">매수 잔량</span>
      </div>
      {asks.length === 0 ? <p className="py-3 text-center text-sm text-muted">매도 호가 없음</p>
        : asks.map((l) => <Row key={`a${l.price}`} price={l.price} qty={l.quantity} side="ask" />)}
      <div className="my-0.5 h-px bg-line" />
      {book.bids.length === 0 ? <p className="py-3 text-center text-sm text-muted">매수 호가 없음</p>
        : book.bids.map((l) => <Row key={`b${l.price}`} price={l.price} qty={l.quantity} side="bid" />)}
      <div className="mt-2 grid grid-cols-[1fr_auto_1fr] items-center gap-1 rounded-xl bg-panel2 py-2 text-sm tabular-nums">
        <span className="pr-2 text-right text-down">{credits(totalAsk)}</span>
        <span className="min-w-[7.5rem] text-center text-xs text-muted">총 잔량</span>
        <span className="pl-2 text-up">{credits(totalBid)}</span>
      </div>
      {onPick && <p className="mt-2 text-xs text-muted">호가를 누르면 주문 가격에 들어가요.</p>}
    </div>
  );
}
