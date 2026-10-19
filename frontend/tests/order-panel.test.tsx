import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OrderPanel } from "@/components/OrderPanel";
import type { Asset } from "@/lib/api";

const asset: Asset = {
  id: 7, symbol: "DTEST", reference_price: 10000, previous_price: 10000, current_price: 10000, change: 0, change_pct: 0,
  shares_outstanding: 1000000, trading_status: "active", halt_reason: null, price_updated_at: null, last_trade_at: null,
  volume_24h: 0, trades_24h: 0, is_demo: true, turnover_24h: 0, market_cap: 10000000000, popularity_score: 25,
  fee_bps: 20, fee_surcharge_reason: null, fee_surcharge_until: null, sparkline: [], tick_size: 10, upper_limit: 13_000, lower_limit: 7_000, vi_until: null, vi_kind: null,
  freshness: { status: "fresh", last_update_at: new Date().toISOString(), age_hours: 0, stale_after_hours: 48 },
  channel: { id: 1, youtube_channel_id: "DEMO_x", title: "[DEMO] X", handle: null, thumbnail_url: null, category: null,
    data_source: "demo", listed_at: null, data_quality_status: "ok", last_data_update_at: null, hidden_subscriber_count: false },
};

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

describe("OrderPanel", () => {
  beforeEach(() => {
    document.cookie = "yex_csrf=csrf-abc";
  });
  afterEach(() => vi.restoreAllMocks());

  it("shows the result only after the server responds, sending CSRF + idempotency key", async () => {
    const d = deferred<Response>();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockReturnValue(d.promise);
    render(<OrderPanel asset={asset} loggedIn availableCash={1000000} availableShares={0} />);
    await userEvent.clear(screen.getByLabelText("수량"));
    await userEvent.type(screen.getByLabelText("수량"), "3");
    await userEvent.click(screen.getByRole("button", { name: "매수 주문" }));

    expect(screen.getByRole("button", { name: "서버 처리 중…" })).toBeDisabled();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/orders");
    const headers = init.headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe("csrf-abc");
    expect(headers["X-Requested-With"]).toBe("yex");
    const body = JSON.parse(init.body as string);
    expect(body).toMatchObject({ asset_id: 7, side: "buy", order_type: "limit", quantity: 3, limit_price: 10000 });
    expect(body.idempotency_key).toBeTruthy();
    expect(body.fee).toBeUndefined();

    d.resolve(new Response(JSON.stringify({
      order: { id: 55, status: "filled", quantity: 3, filled_quantity: 3, remaining_quantity: 0, status_reason: null },
      fills: [{ id: 1, quantity: 3, execution_price: 10000 }], replayed: false,
    }), { status: 201 }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("주문 #55 · 체결 완료"));
  });

  it("retries after a network error with the same idempotency key", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(new TypeError("network down"))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        order: { id: 9, status: "open", quantity: 1, filled_quantity: 0, remaining_quantity: 1, status_reason: null },
        fills: [], replayed: false,
      }), { status: 201 }));
    render(<OrderPanel asset={asset} loggedIn availableCash={1000000} availableShares={0} />);
    await userEvent.click(screen.getByRole("button", { name: "매수 주문" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("네트워크 오류"));
    await userEvent.click(screen.getByRole("button", { name: "매수 주문" }));
    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    const k1 = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string).idempotency_key;
    const k2 = JSON.parse((fetchMock.mock.calls[1][1] as RequestInit).body as string).idempotency_key;
    expect(k1).toBe(k2);
  });

  it("displays server-side rejection messages", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      error: { code: "INSUFFICIENT_FUNDS", message: "Available cash is not enough", details: {} },
    }), { status: 409 }));
    render(<OrderPanel asset={asset} loggedIn availableCash={10} availableShares={0} />);
    await userEvent.click(screen.getByRole("button", { name: "매수 주문" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Available cash is not enough"));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("cannot submit while the asset is halted", () => {
    render(<OrderPanel asset={{ ...asset, trading_status: "halted" }} loggedIn availableCash={1000000} availableShares={0} />);
    expect(screen.getByRole("button", { name: /거래 정지 중/ })).toBeDisabled();
  });
});
