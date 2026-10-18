import { render, screen } from "@testing-library/react";
import { PriceChart } from "@/components/PriceChart";

const bar = (t: string, c: number) => ({ timestamp: t, open_price: c, high_price: c, low_price: c, close_price: c, volume: 0 });

describe("PriceChart", () => {
  it("shows an explicit empty state instead of a fake line", () => {
    render(<PriceChart bars={[]} />);
    expect(screen.getByText("가격 기록이 아직 없습니다")).toBeInTheDocument();
  });
  it("renders a visible marker for a single data point", () => {
    const { container } = render(<PriceChart bars={[bar("2026-01-01T00:00:00Z", 1000)]} />);
    expect(container.querySelector("circle")).not.toBeNull();
  });
  it("renders a line for a series", () => {
    const { container } = render(<PriceChart bars={[bar("2026-01-01T00:00:00Z", 1000), bar("2026-01-02T00:00:00Z", 1100)]} />);
    expect(container.querySelectorAll("path")[1].getAttribute("d")).toMatch(/^M.*L/);
  });
});

describe("CandleChart", () => {
  it("draws candles with moving averages, a trend line and the volume profile", async () => {
    const { CandleChart } = await import("@/components/CandleChart");
    const bars = Array.from({ length: 30 }, (_, i) => ({
      timestamp: new Date(Date.UTC(2026, 0, 1 + i)).toISOString(),
      open_price: 1000 + i * 10, high_price: 1020 + i * 10, low_price: 990 + i * 10, close_price: 1010 + i * 10, volume: 5 + i,
    }));
    const profile = { bins: [{ low: 1000, high: 1099, volume: 40, buy_volume: 25 }], poc_price: 1050, current_price: 1300, total_volume: 40 };
    const { container, getByText } = render(<CandleChart bars={bars} interval="1d" profile={profile} show={{ ma: true, trend: true, profile: true }} />);
    expect(container.querySelectorAll("rect").length).toBeGreaterThan(60); // bodies + volume + profile
    expect(getByText("MA5")).toBeInTheDocument();
    expect(getByText(/추세/)).toHaveTextContent("▲");
    expect(getByText(/최대 매물대/)).toBeInTheDocument();
  });

  it("zooms in and out with the buttons", async () => {
    const { CandleChart } = await import("@/components/CandleChart");
    const { fireEvent } = await import("@testing-library/react");
    const bars = Array.from({ length: 200 }, (_, i) => ({
      timestamp: new Date(Date.UTC(2026, 0, 1, 0, i)).toISOString(),
      open_price: 1000, high_price: 1010, low_price: 990, close_price: 1005, volume: 1,
    }));
    const { getByLabelText, getByText } = render(<CandleChart bars={bars} interval="1m" show={{ ma: false, trend: false, profile: false }} />);
    expect(getByText(/60개 봉 표시/)).toBeInTheDocument();
    fireEvent.click(getByLabelText("확대"));
    expect(getByText(/40개 봉 표시/)).toBeInTheDocument();
    fireEvent.click(getByLabelText("축소"));
    fireEvent.click(getByLabelText("축소"));
    expect(getByText(/90개 봉 표시/)).toBeInTheDocument();
  });
});
