import { render, screen, waitFor } from "@testing-library/react";
import { ChatBox } from "@/components/ChatBox";
import { NotificationBell } from "@/components/NotificationBell";
import { OrderPanel } from "@/components/OrderPanel";
import type { Asset } from "@/lib/api";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

afterEach(() => vi.restoreAllMocks());

describe("ChatBox", () => {
  it("renders messages as text, never as HTML", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      json([{ id: 1, room: "global", username: "kim", body: "<img src=x onerror=alert(1)>", created_at: new Date().toISOString() }]));
    const { container } = render(<ChatBox room="global" />);
    await waitFor(() => expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument());
    expect(container.querySelector("img")).toBeNull();
  });
});

describe("NotificationBell", () => {
  it("shows the unread count", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ unread: 3 }));
    render(<NotificationBell />);
    await waitFor(() => expect(screen.getByRole("button", { name: "알림 3개 읽지 않음" })).toHaveTextContent("3"));
  });
});

describe("OrderPanel outside trading hours", () => {
  const asset = {
    id: 1, symbol: "DX", reference_price: 10000, previous_price: 10000, current_price: 10000, change: 0, change_pct: 0,
    shares_outstanding: 1000, trading_status: "active", halt_reason: null, price_updated_at: null, last_trade_at: null,
    volume_24h: 0, trades_24h: 0, turnover_24h: 0, market_cap: 1, popularity_score: 30, fee_bps: 100,
    fee_surcharge_reason: "new_video", fee_surcharge_until: null, is_demo: true,
    freshness: { status: "fresh", last_update_at: null, age_hours: 0, stale_after_hours: 48 },
    channel: { id: 1, youtube_channel_id: "DEMO_x", title: "x", handle: null, thumbnail_url: null, category: null,
      data_source: "demo", data_quality_status: "ok", last_data_update_at: null, hidden_subscriber_count: false },
  } as Asset;

  it("labels the order as a reservation and shows the surcharge fee", () => {
    render(<OrderPanel asset={asset} loggedIn marketOpen={false} availableCash={1000000} availableShares={0} />);
    expect(screen.getByRole("button", { name: "매수 예약" })).toBeEnabled();
    expect(screen.getByText(/예약 주문으로 접수/)).toBeInTheDocument();
    expect(screen.getByText(/예상 수수료 \(1% 할증\)/)).toBeInTheDocument();
  });
});

describe("ChatBox donation boxes", () => {
  it("shows a box card with progress and marks finished boxes from the live list", async () => {
    const now = new Date().toISOString();
    const box = { id: 7, room: "global", title: "상장비 모아요", goal: 50000, raised: 20000, owner: "kim",
                  state: "open", created_at: now, closes_at: now };
    const done = { ...box, id: 8, title: "끝난 기부함" };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => String(url).includes("/boxes")
      ? json([box])
      : json([
        { id: 1, room: "global", username: "kim", body: "🎁", created_at: now, kind: "donation_box", ref_id: 7, box },
        { id: 2, room: "global", username: "lee", body: "🎁", created_at: now, kind: "donation_box", ref_id: 8, box: done },
        { id: 3, room: "global", username: "park", body: "💝 kim님의 기부함에 5,000 기부", created_at: now, kind: "donation", ref_id: 1 },
      ]));
    render(<ChatBox room="global" />);
    await waitFor(() => expect(screen.getAllByText("상장비 모아요").length).toBeGreaterThan(0));
    expect(screen.getAllByText("40%").length).toBe(2); // both box cards show their progress
    expect(screen.getByText("마감")).toBeInTheDocument(); // no longer in the open list
    expect(screen.getByText(/kim님의 기부함에 5,000 기부/)).toBeInTheDocument();
  });
});
