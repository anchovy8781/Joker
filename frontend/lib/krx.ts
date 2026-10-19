// 호가단위 (KRX tick sizes since 2023) — mirrors backend/app/services/krx.py.
const TICKS: [number, number][] = [[2_000, 1], [5_000, 5], [20_000, 10], [50_000, 50], [200_000, 100], [500_000, 500]];

export function tickSize(price: number): number {
  for (const [bound, tick] of TICKS) if (price < bound) return tick;
  return 1_000;
}
export const floorTick = (p: number) => Math.floor(p / tickSize(p)) * tickSize(p);
export const ceilTick = (p: number) => Math.ceil(p / tickSize(p)) * tickSize(p);
export const roundTick = (p: number) => (p - floorTick(p) < ceilTick(p) - p ? floorTick(p) : ceilTick(p));
/** One tick up or down from an on-tick price. */
export const stepTick = (p: number, dir: 1 | -1) => Math.max(1, dir > 0 ? p + tickSize(p) : p - tickSize(Math.max(1, p - 1)));
