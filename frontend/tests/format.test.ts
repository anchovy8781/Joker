import { credits, direction, estimateFee, pct, signed, timeAgo } from "@/lib/format";

describe("format", () => {
  it("formats credits and signs", () => {
    expect(credits(1000000)).toBe("1,000,000");
    expect(credits(null)).toBe("—");
    expect(signed(1500)).toBe("+1,500");
    expect(signed(-1500)).toBe("−1,500");
    expect(pct(3.3)).toBe("+3.30%");
    expect(pct(-8)).toBe("−8.00%");
    expect(direction(0)).toBe("flat");
  });
  it("mirrors the server fee rounding (ceil, 0.2%)", () => {
    expect(estimateFee(70000)).toBe(140);
    expect(estimateFee(100)).toBe(1);
    expect(estimateFee(501)).toBe(2);
  });
  it("never shows missing timestamps as recent", () => {
    expect(timeAgo(null)).toBe("기록 없음");
    const now = new Date("2026-01-03T00:00:00Z");
    expect(timeAgo("2026-01-01T00:00:00Z", now)).toBe("2일 전");
  });
});
