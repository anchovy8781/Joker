import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LivePanel, type Live } from "@/components/LivePanel";

const now = new Date().toISOString();
const live: Live = {
  server_time: now,
  asset: { id: 1, symbol: "DGAME", price: 12060, previous_price: 12000, change: 60, change_pct: 0.5,
           trading_status: "active", popularity_score: 30, last_trade_at: now },
  game: {
    m1: { trades: 3, volume: 23, value: 277000, buy_ratio: 0.75 },
    m5: { trades: 9, volume: 71, value: 856000, buy_ratio: 0.75 },
    h1: { trades: 9, volume: 71, value: 856000, buy_ratio: 0.75 },
    h24: { trades: 9, volume: 71, value: 856000, buy_ratio: 0.75 },
    best_bid: 12000, best_ask: 12060, spread_bps: 50,
  },
  ticks: [{ t: now, price: 12060, qty: 5, side: "buy" }],
  youtube: { source: "demo", samples: [{ t: now, views: 790000000, subs: 1617000 }], observed_at: now,
             views: 790000000, subscribers: 1617000, videos: 1803, views_per_hour: 230000, subs_per_hour: 157.5,
             est_views_now: 790012345, est_subs_now: 1617010 },
};

describe("LivePanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows exact game activity and flags YouTube estimates", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(live), { status: 200 }));
    render(<LivePanel assetId={1} />);
    expect(await screen.findByText("23주")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("실시간 조회수 (추정)")).toBeInTheDocument();
    expect(screen.getByText("790,012,345")).toBeInTheDocument();
    expect(screen.getByText("2초마다 갱신")).toBeInTheDocument();
    const [url] = (globalThis.fetch as unknown as { mock: { calls: [string][] } }).mock.calls[0];
    expect(url).toBe("/api/assets/1/live");
  });
});
