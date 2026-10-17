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
