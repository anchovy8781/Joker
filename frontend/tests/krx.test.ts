import { describe, expect, it } from "vitest";
import { ceilTick, floorTick, roundTick, stepTick, tickSize } from "@/lib/krx";

describe("호가단위", () => {
  it("follows the KRX table", () => {
    expect([1_999, 2_000, 5_000, 20_000, 50_000, 200_000, 500_000].map(tickSize)).toEqual([1, 5, 10, 50, 100, 500, 1000]);
  });
  it("rounds and steps on the grid, across a tick boundary", () => {
    expect(floorTick(20_049)).toBe(20_000);
    expect(ceilTick(19_995)).toBe(20_000);
    expect(roundTick(10_004)).toBe(10_000);
    expect(stepTick(20_000, -1)).toBe(19_990);
    expect(stepTick(19_990, 1)).toBe(20_000);
  });
});
