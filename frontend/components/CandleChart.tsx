"use client";

import { useMemo, useState } from "react";
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
const MA = [
  { n: 5, color: "#b88a14" },
  { n: 20, color: "#8b6fe0" },
  { n: 60, color: "#1fa97a" },
] as const;

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

export function CandleChart({ bars, interval, profile, show }: {
  bars: Bar[];
  interval: string;
  profile?: VolumeProfile | null;
  show: { ma: boolean; trend: boolean; profile: boolean };
}) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 800, PRICE_H = 270, VOL_TOP = 290, VOL_H = 60, H = 372;
  const pad = { l: 6, r: 70 };
  const plotW = W - pad.l - pad.r;

  const g = useMemo(() => {
    if (bars.length === 0) return null;
    const closes = bars.map((b) => b.close_price);
    const mas = MA.map((m) => ({ ...m, values: sma(closes, m.n) }));
    const t = trend(closes);
    let min = Math.min(...bars.map((b) => b.low_price));
    let max = Math.max(...bars.map((b) => b.high_price));
    if (show.profile && profile?.bins.length) {
      min = Math.min(min, profile.bins[0].low);
      max = Math.max(max, profile.bins[profile.bins.length - 1].high);
    }
    if (min === max) { min *= 0.99; max *= 1.01; }
    const padP = (max - min) * 0.06;
    min -= padP; max += padP;
    const slot = plotW / bars.length;
    const x = (i: number) => pad.l + slot * (i + 0.5);
    const y = (v: number) => 8 + (1 - (v - min) / (max - min)) * (PRICE_H - 16);
    const maxVol = Math.max(1, ...bars.map((b) => b.volume));
    const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => min + (max - min) * f);
    return { closes, mas, t, min, max, slot, x, y, maxVol, ticks };
  }, [bars, profile, show.profile, plotW]);

  if (!g) return <div className="flex h-56 items-center justify-center text-sm text-muted">아직 거래 기록이 없어요</div>;

  const body = Math.max(1.5, Math.min(14, g.slot * 0.62));
  const hb = hover !== null ? bars[hover] : bars[bars.length - 1];
  const hi = hover ?? bars.length - 1;
  const last = bars[bars.length - 1].close_price;
  const fmt = (iso: string) => {
    const d = new Date(iso);
    if (interval === "1mo") return d.toLocaleDateString("ko-KR", { year: "2-digit", month: "short" });
    if (interval === "1d" || interval === "1w") return d.toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" });
    if (interval.endsWith("s")) return d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    return d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
  };
  const xTicks = bars.length <= 1 ? [0] : [0, Math.round((bars.length - 1) / 2), bars.length - 1];
  const maxBin = Math.max(1, ...(profile?.bins.map((b) => b.volume) ?? [1]));
  const up = hb.close_price >= hb.open_price;

  return (
    <div>
      {/* readout + legend (identity never by colour alone: names sit next to swatches) */}
      <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        <span className="text-muted">{new Date(hb.timestamp).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: interval === "1d" || interval === "1w" || interval === "1mo" ? undefined : "short" })}</span>
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
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full touch-none select-none" role="img"
        aria-label={`캔들 차트, 마지막 종가 ${credits(last)}`}
        onMouseLeave={() => setHover(null)}
        onPointerMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const px = ((e.clientX - r.left) / r.width) * W;
          setHover(Math.max(0, Math.min(bars.length - 1, Math.floor((px - pad.l) / g.slot))));
        }}>
        {/* grid + price axis */}
        {g.ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={W - pad.r} y1={g.y(t)} y2={g.y(t)} stroke="#2c2c35" strokeWidth="1" />
            <text x={W - pad.r + 8} y={g.y(t) + 4} fill="#8b95a1" fontSize="12">{credits(t)}</text>
          </g>
        ))}

        {/* 매물대: traded volume by price band, drawn from the right edge of the plot */}
        {show.profile && profile?.bins.map((b) => {
          const y1 = g.y(b.high + 1), y2 = g.y(b.low);
          const w = (b.volume / maxBin) * plotW * 0.32;
          const buyW = (b.buy_volume / maxBin) * plotW * 0.32;
          const poc = profile.poc_price !== null && profile.poc_price >= b.low && profile.poc_price <= b.high;
          const h = Math.max(1, y2 - y1 - 2);
          return (
            <g key={b.low} opacity={poc ? 0.55 : 0.32}>
              <rect x={W - pad.r - w} y={y1 + 1} width={w - buyW} height={h} fill={DOWN} rx="2" />
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

        {/* moving averages */}
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
        <text x={W - pad.r / 2} y={g.y(last) + 4} fill="#fff" fontSize="12" fontWeight="700" textAnchor="middle">{compact(last)}</text>

        {/* x axis */}
        <line x1={pad.l} x2={W - pad.r} y1={VOL_TOP - 6} y2={VOL_TOP - 6} stroke="#2c2c35" />
        <text x={W - pad.r + 8} y={VOL_TOP + 12} fill="#8b95a1" fontSize="11">거래량</text>
        {xTicks.map((i) => (
          <text key={i} x={g.x(i)} y={H - 4} fill="#8b95a1" fontSize="12"
            textAnchor={i === 0 ? "start" : i === bars.length - 1 ? "end" : "middle"}>{fmt(bars[i].timestamp)}</text>
        ))}

        {/* crosshair */}
        {hover !== null && (
          <g pointerEvents="none">
            <line x1={g.x(hover)} x2={g.x(hover)} y1={0} y2={VOL_TOP + VOL_H} stroke="#8b95a1" strokeWidth="1" />
            <circle cx={g.x(hover)} cy={g.y(bars[hover].close_price)} r="4.5" fill={up ? UP : DOWN} stroke="#202027" strokeWidth="2" />
          </g>
        )}
      </svg>
      {show.trend && g.t && (
        <p className="mt-1 text-xs text-muted">
          추세선(점선): <b className={g.t.pctPerBar >= 0 ? "text-up" : "text-down"}>{g.t.pctPerBar >= 0 ? "▲ 상승" : "▼ 하락"} {Math.abs(g.t.pctPerBar).toFixed(2)}%/봉</b>
          {show.profile && profile?.poc_price ? <> · 최대 매물대 <b className="text-text">₩{credits(profile.poc_price)}</b></> : null}
        </p>
      )}
    </div>
  );
}
