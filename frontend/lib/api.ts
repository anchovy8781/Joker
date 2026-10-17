// Thin client for the FastAPI backend (proxied at /api by next.config.mjs).
// Every state-changing request carries the CSRF token (double-submit, session-bound)
// and the X-Requested-With header the server requires.

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const m = document.cookie.split("; ").find((c) => c.startsWith(name + "="));
  return m ? decodeURIComponent(m.slice(name.length + 1)) : null;
}

export async function api<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const method = init.method ?? (init.body !== undefined ? "POST" : "GET");
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["X-Requested-With"] = "yex";
    const csrf = readCookie("yex_csrf");
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }
  const res = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    cache: "no-store",
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  if (res.status === 204) return undefined as T;
  let data: any = null;
  try {
    data = await res.json();
  } catch {
    /* non-JSON */
  }
  if (!res.ok) {
    const e = data?.error;
    throw new ApiError(res.status, e?.code ?? "HTTP_ERROR", e?.message ?? `Request failed (${res.status})`, e?.details ?? {});
  }
  return data as T;
}

export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

// ---------------------------------------------------------------- types
export type User = {
  id: number;
  username: string;
  role: "user" | "admin";
  status: string;
  risk_level: string;
  verification_status: string;
};

export type Freshness = {
  status: "fresh" | "stale" | "never";
  last_update_at: string | null;
  age_hours: number | null;
  stale_after_hours: number;
};

export type Channel = {
  id: number;
  youtube_channel_id: string;
  title: string;
  handle: string | null;
  thumbnail_url: string | null;
  category: string | null;
  data_source: "youtube_api" | "demo";
  listed_at: string | null;
  data_quality_status: string;
  last_data_update_at: string | null;
  hidden_subscriber_count: boolean;
};

export type Asset = {
  id: number;
  symbol: string;
  channel: Channel;
  reference_price: number;
  previous_price: number;
  current_price: number;
  change: number;
  change_pct: number;
  shares_outstanding: number;
  trading_status: "active" | "halted";
  halt_reason: string | null;
  price_updated_at: string | null;
  last_trade_at: string | null;
  volume_24h: number;
  trades_24h: number;
  turnover_24h: number;
  market_cap: number;
  popularity_score: number | null;
  fee_bps: number;
  fee_surcharge_reason: "new_video" | "celebrity_new_channel" | null;
  fee_surcharge_until: string | null;
  freshness: Freshness;
  is_demo: boolean;
  sparkline: number[];
};

export type Bar = { timestamp: string; open_price: number; high_price: number; low_price: number; close_price: number; volume: number };
export type BookLevel = { price: number; quantity: number; orders: number };
export type OrderBook = { asset_id: number; bids: BookLevel[]; asks: BookLevel[] };
export type Trade = { id: number; asset_id: number; quantity: number; execution_price: number; taker_side: "buy" | "sell"; executed_at: string; symbol?: string };
export type MyTrade = Trade & { side: "buy" | "sell"; fee: number; fee_waived: boolean; realized_pnl: string | null; symbol: string };

export type Order = {
  id: number;
  asset_id: number;
  side: "buy" | "sell";
  order_type: "market" | "limit";
  quantity: number;
  remaining_quantity: number;
  filled_quantity: number;
  limit_price: number | null;
  reserved_cash: number;
  fee_bps: number;
  status: string;
  status_reason: string | null;
  created_at: string;
};

export type Account = {
  cash_balance: number;
  reserved_cash: number;
  available_cash: number;
  positions_value: number;
  total_equity: number;
  realized_pnl: string;
  unrealized_pnl: string;
  positions_count: number;
  note: string;
};

export type Position = {
  asset_id: number;
  symbol: string;
  title: string;
  is_demo: boolean;
  quantity: number;
  reserved_quantity: number;
  average_cost: string;
  current_price: number;
  market_value: number;
  unrealized_pnl: string;
  realized_pnl: string;
};

export type LedgerEntry = { id: number; entry_type: string; amount: number; balance_after: number; reference_type: string | null; reference_id: string | null; memo: string | null; created_at: string };
export type MetricPoint = { observed_date: string; subscriber_count: number | null; view_count: number | null; video_count: number | null; source: string };
export type LeaderRow = { rank: number; username: string; total_equity: number; cash_balance: number; positions_value: number; return_pct: number };
export type Notification = { id: number; kind: string; title: string; body: string; link: string | null; created_at: string; read: boolean };
export type Mission = { code: string; title: string; description: string; reward: number; period: "once" | "daily"; target: number; progress: number; claimed: boolean; claimable: boolean };
export type DonationBoxInfo = { id: number; room: string; title: string; goal: number; raised: number; owner: string; state: "open" | "closed" | "expired"; created_at: string; closes_at: string };
export type ChatMessage = { id: number; room: string; username: string; body: string; created_at: string; kind?: "text" | "donation_box" | "donation"; ref_id?: number | null; box?: DonationBoxInfo | null };
export type PriceAlert = { id: number; asset_id: number; symbol: string; direction: "above" | "below"; target_price: number; current_price: number; created_at: string; triggered_at: string | null; active: boolean };
export type ExpertPick = { rank: number; expert_buyers: number; net_buy_notional: number; asset: Asset };
export type ListingRequest = { id: number; youtube_channel_id: string; title: string; listing_status: string; created_at: string; note: string | null; fee_paid: number };
export type AutoOrder = { id: number; asset_id: number; side: "buy" | "sell"; trigger_condition: "lte" | "gte"; trigger_price: number; order_type: "market" | "limit"; limit_price: number | null; quantity: number; status: string; expires_at: string; order_id: number | null; failure_reason: string | null; created_at: string };
export type MarketHours = { open: boolean; enforced: boolean; session_open: string; session_close: string; next_open: string; next_close: string; schedule: { weekday: string; open: string; close: string }[]; note: string };
export type DonationBox = { box: { daily_limit: number; received_today: number; sent_today: number; receive_remaining: number; send_remaining: number; full: boolean }; history: { id: number; amount: number; message: string | null; created_at: string; sender: string; recipient: string; outgoing: boolean }[]; my_boxes?: { can_open_today: boolean; boxes: DonationBoxInfo[] } };
export type ListingWindow = { open: boolean; opens_at: string | null; next_opens_at: string; limit: number; used: number; remaining: number; limit_reached: boolean; listing_fee: number };
