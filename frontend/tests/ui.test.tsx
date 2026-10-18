import { render, screen } from "@testing-library/react";
import { Change, DemoBadge, FreshnessBadge, TradingBadge } from "@/components/ui";

describe("status indicators", () => {
  it("price change uses arrow + text, not colour alone", () => {
    const { rerender } = render(<Change percent={2.5} value={250} />);
    expect(screen.getByLabelText("상승 +2.50%")).toHaveTextContent("▲");
    rerender(<Change percent={-1} />);
    expect(screen.getByLabelText("하락 −1.00%")).toHaveTextContent("▼");
  });
  it("labels demo vs real data", () => {
    const { rerender } = render(<DemoBadge isDemo />);
    expect(screen.getByText("DEMO 데이터")).toBeInTheDocument();
    rerender(<DemoBadge isDemo={false} />);
    expect(screen.getByText("실제 데이터")).toBeInTheDocument();
  });
  it("stale data is flagged as stale", () => {
    render(<FreshnessBadge f={{ status: "stale", last_update_at: "2020-01-01T00:00:00Z", age_hours: 9999, stale_after_hours: 48 }} />);
    expect(screen.getByText(/오래된 데이터/)).toBeInTheDocument();
    expect(screen.queryByText(/최신/)).not.toBeInTheDocument();
  });
  it("halted badge has text and icon", () => {
    render(<TradingBadge status="halted" reason="AUTO:STALE_DATA" />);
    expect(screen.getByText(/거래 정지/)).toHaveTextContent("⏸");
  });
});
