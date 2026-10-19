export function credits(n: number | string | null | undefined): string {
  if (n === null || n === undefined || n === "") return "—";
  const v = typeof n === "string" ? Number(n) : n;
  return Math.round(v).toLocaleString("ko-KR");
}

export function signed(n: number | string): string {
  const v = typeof n === "string" ? Number(n) : n;
  const r = Math.round(v);
  return (r > 0 ? "+" : r < 0 ? "−" : "") + Math.abs(r).toLocaleString("ko-KR");
}

export function pct(n: number): string {
  const s = Math.abs(n).toFixed(2);
  return (n > 0 ? "+" : n < 0 ? "−" : "") + s + "%";
}

export function compact(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return new Intl.NumberFormat("ko-KR", { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

export function direction(n: number): "up" | "down" | "flat" {
  return n > 0 ? "up" : n < 0 ? "down" : "flat";
}

export function timeAgo(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "기록 없음";
  const diff = (now.getTime() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "방금 전";
  if (diff < 3600) return `${Math.floor(diff / 60)}분 전`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}시간 전`;
  return `${Math.floor(diff / 86400)}일 전`;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" });
}

// Server fee rule mirrored for *display only*; the server computes the real fee.
export function estimateFee(notional: number, feeBps = 20): number {
  return Math.ceil((notional * feeBps) / 10000);
}

const ORDER_STATUS: Record<string, string> = {
  scheduled: "예약",
  open: "대기",
  partially_filled: "부분 체결",
  filled: "체결 완료",
  canceled: "취소",
  rejected: "거부",
};
export const orderStatusLabel = (s: string) => ORDER_STATUS[s] ?? s;

const REASONS: Record<string, string> = {
  NO_LIQUIDITY: "허용 가격 범위 안에 거래 상대가 없습니다",
  PARTIAL_FILL_REMAINDER_CANCELED: "가격 보호 범위를 넘는 잔량은 취소되었습니다",
  SELF_TRADE_PREVENTED: "본인 주문과의 체결이 방지되었습니다",
  USER_CANCELED: "사용자 취소",
  MATCH_DEPTH_LIMIT: "한 번에 체결 가능한 주문 수를 초과해 잔량이 취소되었습니다",
  RESERVED_UNTIL_OPEN: "장 시작 시 접수 순서대로 제출됩니다",
  VI_CALL_AUCTION: "VI 발동 · 단일가 매매로 모아서 한 가격에 체결됩니다",
  OUT_OF_BAND_AT_OPEN: "장 시작 시 지정가가 가격 제한 범위를 벗어나 취소되었습니다",
};
export const reasonLabel = (r: string | null) => (r ? REASONS[r] ?? r : "");

export function kstTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("ko-KR", { timeZone: "Asia/Seoul", weekday: "short", hour: "2-digit", minute: "2-digit" });
}

export function popularityLabel(score: number | null | undefined): string {
  if (!score) return "—";
  if (score >= 40) return "🔥 매우 높음";
  if (score >= 30) return "높음";
  if (score >= 20) return "보통";
  if (score >= 10) return "낮음";
  return "매우 낮음";
}
