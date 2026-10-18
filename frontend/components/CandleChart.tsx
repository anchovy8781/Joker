"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { Bar } from "@/lib/api";
import { compact, credits } from "@/lib/format";

export type VolumeProfile = {
  bins: { low: number; high: number; volume: number; buy_volume: number }[];
  poc_price: number | null;
  current_price: number;
  total_volume: number;
};

// Validated (dark surface, CVD-safe) — rises/falls keep the market's red/blue.
const UP = "#f04452";
const DOWN = "#3182f6";
const GRID = "#2c2c35";
const AXIS = "#8b95a1";
const MA = [
  { n: 5, color: "#b88a14" },
  { n: 20, color: "#8b6fe0" },
  { n: 60, color: "#1fa97a" },
] as const;
const MIN_VISIBLE = 12;
const DEFAULT_VISIBLE = 60;

function sma(values: number[], n: number): (number | null)[] {
  let sum = 0;
  return values.map((v, i) => {
    sum += v;
    if (i >= n) sum -= values[i - n];
    return i >= n - 1 ? sum / n : null;
  });
}

/** Least-squares line through the closes: the trend over the visible range. */
function trend(values: number[]): { a: number; b: number; pctPerBar: number } | null {
  const n = values.length;
  if (n < 3) return null;
  const mx = (n - 1) / 2;
  const my = values.reduce((s, v) => s + v, 0) / n;
  let num = 0, den = 0;
  values.forEach((v, i) => { num += (i - mx) * (v - my); den += (i - mx) ** 2; });
  const b = den ? num / den : 0;
  return { a: my - b * mx, b, pctPerBar: my ? (b / my) * 100 : 0 };
}

/** Candles with pinch / wheel / ± zoom and drag to scroll back in time. While the view is
 *  at the right edge it follows new candles; scrolled back it stays where it is. */
export function CandleChart({ bars: all, interval, profile, show }: {
  bars: Bar[];
  interval: string;
  profile?: VolumeProfile | null;
  show: { ma: boolean; trend: boolean; profile: boolean };
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [visible, setVisible] = useState(DEFAULT_VISIBLE);
  const [offset, setOffset] = useState(0); // candles hidden on the right (0 = live edge)
  const svgRef = useRef<SVGSVGElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  // Drawn at the real on-screen width, so labels stay readable on phones (no shrinking).
  const [W, setW] = useState(800);
  useEffect(() => {
    const el = boxRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [all.length > 0]); // the empty-state box and the chart are different elements
  const PRICE_H = W < 500 ? 230 : 270, VOL_TOP = PRICE_H + 20, VOL_H = W < 500 ? 50 : 60, H = VOL_TOP + VOL_H + 22;
  const pad = { l: 4, r: W < 500 ? 58 : 70 };
  const plotW = W - pad.l - pad.r;

  // A new interval starts at the live edge with the default zoom.
  useEffect(() => { setVisible(DEFAULT_VISIBLE); setOffset(0); setHover(null); }, [interval]);

  const n = all.length;
  const count = Math.max(1, Math.min(n, Math.max(MIN_VISIBLE, visible)));
  const off = Math.max(0, Math.min(offset, n - count));
  const from = Math.max(0, n - count - off);
  const bars = useMemo(() => all.slice(from, from + count), [all, from, count]);

  const g = useMemo(() => {
    if (bars.length === 0) return null;
    const closesAll = all.map((b) => b.close_price);
    const mas = MA.map((m) => ({ ...m, values: sma(closesAll, m.n).slice(from, from + bars.length) }));
    const closes = bars.map((b) => b.close_price);
    const t = trend(closes);
    // Scale to the visible candles only, so a zoomed-in view fills the height.
    let min = Math.min(...bars.map((b) => b.low_price));
    let max = Math.max(...bars.map((b) => b.high_price));
    if (min === max) { const d = Math.max(1, min * 0.002); min -= d; max += d; }
    const padP = (max - min) * 0.08;
    min -= padP; max += padP;
    const slot = plotW / bars.length;
    const x = (i: number) => pad.l + slot * (i + 0.5);
    const y = (v: number) => 8 + (1 - (v - min) / (max - min)) * (PRICE_H - 16);
    const maxVol = Math.max(1, ...bars.map((b) => b.volume));
    const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => min + (max - min) * f);
    return { mas, t, min, max, slot, x, y, maxVol, ticks };
  }, [all, bars, from, plotW, PRICE_H, pad.l]);

  // ---- gestures: one finger / mouse drags in time, two fingers pinch, wheel zooms
  const pointers = useRef(new Map<number, number>());
  const gesture = useRef<{ x: number; offset: number; dist: number; visible: number; moved: boolean } | null>(null);
  const toPlotX = (clientX: number) => {
    const r = svgRef.current!.getBoundingClientRect();
    return ((clientX - r.left) / r.width) * W;
  };
  const zoomBy = (factor: number) => setVisible((v) => Math.round(Math.max(MIN_VISIBLE, Math.min(n || v, v * factor))));

  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => { e.preventDefault(); zoomBy(e.deltaY > 0 ? 1.15 : 1 / 1.15); };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

  if (!g) return <div ref={boxRef} className="flex h-56 items-center justify-center text-sm text-muted">아직 거래 기록이 없어요</div>;

  const onDown = (e: React.PointerEvent<SVGSVGElement>) => {
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    pointers.current.set(e.pointerId, e.clientX);
    const xs = [...pointers.current.values()];
    gesture.current = { x: e.clientX, offset: off, visible: count, moved: false,
      dist: xs.length >= 2 ? Math.abs(xs[0] - xs[1]) : 0 };
  };
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const gs = gesture.current;
    if (!pointers.current.has(e.pointerId) || !gs) {
      if (e.pointerType === "mouse") setHover(Math.max(0, Math.min(bars.length - 1, Math.floor((toPlotX(e.clientX) - pad.l) / g.slot))));
      return;
    }
    pointers.current.set(e.pointerId, e.clientX);
    const xs = [...pointers.current.values()];
    if (xs.length >= 2 && gs.dist > 0) {
      const d = Math.abs(xs[0] - xs[1]);
      if (d > 0) setVisible(Math.round(Math.max(MIN_VISIBLE, Math.min(n, gs.visible * (gs.dist / d)))));
      gs.moved = true;
      return;
    }
    const r = svgRef.current!.getBoundingClientRect();
    const dxBars = ((e.clientX - gs.x) / r.width) * W / g.slot;
    if (Math.abs(e.clientX - gs.x) > 6) gs.moved = true;
    if (gs.moved) {
      setOffset(Math.max(0, Math.min(n - count, Math.round(gs.offset + dxBars))));
      setHover(null);
    }
  };
  const onUp = (e: React.PointerEvent<SVGSVGElement>) => {
    const gs = gesture.current;
    pointers.current.delete(e.pointerId);
    if (gs && !gs.moved && pointers.current.size === 0) {
      // A tap shows the crosshair on that candle (tap again outside to clear).
      const i = Math.floor((toPlotX(e.clientX) - pad.l) / g.slot);
      setHover(i >= 0 && i < bars.length ? i : null);
    }
    if (pointers.current.size === 0) gesture.current = null;
    else {
      const xs = [...pointers.current.values()];
      gesture.current = { x: xs[0], offset: off, visible: count, moved: true, dist: xs.length >= 2 ? Math.abs(xs[0] - xs[1]) : 0 };
    }
  };

  const body = Math.max(1, Math.min(14, g.slot * 0.62));
  const hi = hover ?? bars.length - 1;
  const hb = bars[hi];
  const last = bars[bars.length - 1].close_price;
  const live = off === 0;
  const fmt = (iso: string) => {
    const d = new Date(iso);
    if (interval === "1mo") return d.toLocaleDateString("ko-KR", { year: "2-digit", month: "short" });
    if (interval === "1d" || interval === "1w") return d.toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" });
    if (interval.endsWith("s")) return d.toLocaleTimeString("ko-KR", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
    return d.toLocaleTimeString("ko-KR", { hour12: false, hour: "2-digit", minute: "2-digit" });
  };
  // Four evenly spaced time labels across the visible candles, always in time order.
  const parts = W < 500 ? 2 : 3;
  const xTicks = bars.length <= 1 ? [0] : [...new Set(Array.from({ length: parts + 1 }, (_, k) => Math.round((k * (bars.length - 1)) / parts)))];
  const maxBin = Math.max(1, ...(profile?.bins.map((b) => b.volume) ?? [1]));
  const up = hb.close_price >= hb.open_price;

  return (
    <div>
      {/* readout + legend (identity never by colour alone: names sit next to swatches) */}
      <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        <span className="text-muted">{new Date(hb.timestamp).toLocaleString("ko-KR", { hour12: false, dateStyle: "short", timeStyle: interval === "1d" || interval === "1w" || interval === "1mo" ? undefined : interval.endsWith("s") ? "medium" : "short" })}</span>
        <span>시 <b className="tabular-nums">{credits(hb.open_price)}</b></span>
        <span>고 <b className="tabular-nums text-up">{credits(hb.high_price)}</b></span>
        <span>저 <b className="tabular-nums text-down">{credits(hb.low_price)}</b></span>
        <span>종 <b className={`tabular-nums ${up ? "text-up" : "text-down"}`}>{credits(hb.close_price)}</b></span>
        <span className="text-muted">거래량 <b className="text-text">{compact(hb.volume)}</b></span>
      </div>
      {show.ma && (
        <div className="mb-1 flex flex-wrap gap-3 text-xs">
          {g.mas.map((m) => (
            <span key={m.n} className="flex items-center gap-1 text-muted">
              <span className="inline-block h-0.5 w-4 rounded-full" style={{ background: m.color }} />
              MA{m.n} <b className="tabular-nums text-text">{m.values[hi] ? credits(m.values[hi]!) : "—"}</b>
            </span>
          ))}
        </div>
      )}
      <div ref={boxRef} className="relative">
        <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="h-auto w-full select-none [touch-action:pan-y]" role="img"
          aria-label={`캔들 차트, 마지막 종가 ${credits(last)}. 두 손가락으로 확대, 끌어서 이전 기록 보기`}
          onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}
          onMouseLeave={() => { if (!gesture.current) setHover(null); }}
          onDoubleClick={() => { setVisible(DEFAULT_VISIBLE); setOffset(0); }}>
          {/* grid + price axis */}
          {g.ticks.map((t) => (
            <g key={t}>
              <line x1={pad.l} x2={W - pad.r} y1={g.y(t)} y2={g.y(t)} stroke={GRID} strokeWidth="1" />
              <text x={W - pad.r + 8} y={g.y(t) + 4} fill={AXIS} fontSize="11">{credits(t)}</text>
            </g>
          ))}

          {/* 매물대: traded volume by price band, drawn from the right edge of the plot */}
          {show.profile && profile?.bins.filter((b) => b.high >= g.min && b.low <= g.max).map((b) => {
            const y1 = g.y(Math.min(g.max, b.high + 1)), y2 = g.y(Math.max(g.min, b.low));
            const w = (b.volume / maxBin) * plotW * 0.32;
            const buyW = (b.buy_volume / maxBin) * plotW * 0.32;
            const poc = profile.poc_price !== null && profile.poc_price >= b.low && profile.poc_price <= b.high;
            const h = Math.max(1, y2 - y1 - 2);
            return (
              <g key={b.low} opacity={poc ? 0.55 : 0.32}>
                <rect x={W - pad.r - w} y={y1 + 1} width={Math.max(0, w - buyW)} height={h} fill={DOWN} rx="2" />
                <rect x={W - pad.r - buyW} y={y1 + 1} width={buyW} height={h} fill={UP} rx="2" />
              </g>
            );
          })}

          {/* candles */}
          {bars.map((b, i) => {
            const rise = b.close_price >= b.open_price;
            const c = rise ? UP : DOWN;
            const top = g.y(Math.max(b.open_price, b.close_price));
            const bot = g.y(Math.min(b.open_price, b.close_price));
            return (
              <g key={b.timestamp}>
                <line x1={g.x(i)} x2={g.x(i)} y1={g.y(b.high_price)} y2={g.y(b.low_price)} stroke={c} strokeWidth="1.2" />
                <rect x={g.x(i) - body / 2} y={top} width={body} height={Math.max(1.5, bot - top)} fill={c} rx="1" />
                <rect x={g.x(i) - body / 2} y={VOL_TOP + VOL_H - (b.volume / g.maxVol) * VOL_H} width={body}
                  height={Math.max(b.volume ? 1 : 0, (b.volume / g.maxVol) * VOL_H)} fill={c} opacity="0.55" rx="1" />
              </g>
            );
          })}

          {/* moving averages (computed on the whole series, drawn for the visible part) */}
          {show.ma && g.mas.map((m) => {
            const d = m.values.map((v, i) => (v === null ? null : `${g.x(i).toFixed(1)},${g.y(v).toFixed(1)}`))
              .filter(Boolean).map((p, i) => `${i ? "L" : "M"}${p}`).join(" ");
            return d ? <path key={m.n} d={d} fill="none" stroke={m.color} strokeWidth="2" strokeLinejoin="round" /> : null;
          })}

          {/* trend line (least squares over the visible candles) */}
          {show.trend && g.t && (
            <line x1={g.x(0)} y1={g.y(g.t.a)} x2={g.x(bars.length - 1)} y2={g.y(g.t.a + g.t.b * (bars.length - 1))}
              stroke="#f2f4f6" strokeWidth="1.6" strokeDasharray="6 5" opacity="0.8" />
          )}

          {/* last price tag */}
          <line x1={pad.l} x2={W - pad.r} y1={g.y(last)} y2={g.y(last)} stroke="#f2f4f6" strokeDasharray="2 4" opacity="0.5" />
          <rect x={W - pad.r + 2} y={g.y(last) - 10} width={pad.r - 4} height="20" rx="10" fill={bars.length > 1 && last >= bars[0].open_price ? UP : DOWN} />
          <text x={W - pad.r / 2} y={g.y(last) + 4} fill="#fff" fontSize="12" fontWeight="700" textAnchor="middle">{credits(last).length <= 7 ? credits(last) : compact(last)}</text>

          {/* x axis */}
          <line x1={pad.l} x2={W - pad.r} y1={VOL_TOP - 6} y2={VOL_TOP - 6} stroke={GRID} />
          <text x={W - pad.r + 8} y={VOL_TOP + 12} fill={AXIS} fontSize="11">거래량</text>
          {xTicks.map((i, k) => (
            <text key={i} x={g.x(i)} y={H - 4} fill={AXIS} fontSize="11"
              textAnchor={k === 0 ? "start" : k === xTicks.length - 1 ? "end" : "middle"}>{fmt(bars[i].timestamp)}</text>
          ))}

          {/* crosshair */}
          {hover !== null && (
            <g pointerEvents="none">
              <line x1={g.x(hover)} x2={g.x(hover)} y1={0} y2={VOL_TOP + VOL_H} stroke={AXIS} strokeWidth="1" />
              <circle cx={g.x(hover)} cy={g.y(bars[hover].close_price)} r="4.5" fill={up ? UP : DOWN} stroke="#202027" strokeWidth="2" />
            </g>
          )}
        </svg>
        {/* zoom controls */}
        <div className="absolute left-1 top-1 flex gap-1">
          <button type="button" aria-label="확대" onClick={() => zoomBy(1 / 1.5)}
            className="h-8 w-8 rounded-lg bg-panel2/90 text-base font-bold text-text">＋</button>
          <button type="button" aria-label="축소" onClick={() => zoomBy(1.5)}
            className="h-8 w-8 rounded-lg bg-panel2/90 text-base font-bold text-text">－</button>
          {(!live || count !== Math.min(n, DEFAULT_VISIBLE)) && (
            <button type="button" onClick={() => { setVisible(DEFAULT_VISIBLE); setOffset(0); }}
              className="h-8 rounded-lg bg-primary px-2.5 text-xs font-bold text-white">최신으로</button>
          )}
        </div>
      </div>
      <p className="mt-1 text-xs text-muted">
        {count}개 봉 표시{!live && " · 이전 기록 보는 중"} · 두 손가락으로 확대/축소, 옆으로 끌어서 이전 기록
        {show.trend && g.t && (
          <> · 추세 <b className={g.t.pctPerBar >= 0 ? "text-up" : "text-down"}>{g.t.pctPerBar >= 0 ? "▲" : "▼"} {Math.abs(g.t.pctPerBar).toFixed(2)}%/봉</b></>
        )}
        {show.profile && profile?.poc_price ? <> · 최대 매물대 <b className="text-text">₩{credits(profile.poc_price)}</b></> : null}
      </p>
    </div>
  );
}
