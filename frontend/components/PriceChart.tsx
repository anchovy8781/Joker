"use client";

import { useMemo, useState } from "react";
import type { Bar } from "@/lib/api";
import { credits } from "@/lib/format";

/** Dependency-free SVG close-price chart with hover readout. */
export function PriceChart({ bars, height = 240 }: { bars: Bar[]; height?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 800;
  const H = height;
  const pad = { l: 8, r: 64, t: 12, b: 22 };

  const geo = useMemo(() => {
    if (bars.length === 0) return null;
    const closes = bars.map((b) => b.close_price);
    const lows = bars.map((b) => b.low_price);
    const highs = bars.map((b) => b.high_price);
    let min = Math.min(...lows);
    let max = Math.max(...highs);
    if (min === max) { min *= 0.99; max *= 1.01; }
    const x = (i: number) => pad.l + (bars.length === 1 ? (W - pad.l - pad.r) / 2 : (i / (bars.length - 1)) * (W - pad.l - pad.r));
    const y = (v: number) => pad.t + (1 - (v - min) / (max - min)) * (H - pad.t - pad.b);
    const line = closes.map((c, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(c).toFixed(1)}`).join(" ");
    const area = `${line} L${x(bars.length - 1).toFixed(1)},${H - pad.b} L${x(0).toFixed(1)},${H - pad.b} Z`;
    const up = closes[closes.length - 1] >= closes[0];
    const ticks = [max, (max + min) / 2, min];
    return { x, y, line, area, up, ticks };
  }, [bars, H]);

  if (!geo) return <div className="flex h-40 items-center justify-center text-sm text-muted">가격 기록이 아직 없습니다</div>;
  const color = geo.up ? "#22c55e" : "#ef4444";
  const hb = hover !== null ? bars[hover] : null;

  return (
    <div className="relative">
      {hb && (
        <div className="absolute left-2 top-0 rounded bg-panel2/90 px-2 py-1 font-mono text-xs text-text">
          {new Date(hb.timestamp).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" })} · 시 {credits(hb.open_price)} 고 {credits(hb.high_price)} 저 {credits(hb.low_price)} 종 {credits(hb.close_price)} · 거래량 {credits(hb.volume)}
        </div>
      )}
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label="가격 차트"
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const px = ((e.clientX - r.left) / r.width) * W;
          const i = Math.round(((px - pad.l) / (W - pad.l - pad.r)) * (bars.length - 1));
          setHover(Math.max(0, Math.min(bars.length - 1, i)));
        }}>
        <defs>
          <linearGradient id="fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.25" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        {geo.ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={W - pad.r} y1={geo.y(t)} y2={geo.y(t)} stroke="#232b3b" strokeDasharray="3 4" />
            <text x={W - pad.r + 6} y={geo.y(t) + 4} fill="#8b95a7" fontSize="11" fontFamily="monospace">{credits(t)}</text>
          </g>
        ))}
        <path d={geo.area} fill="url(#fill)" />
        <path d={geo.line} fill="none" stroke={color} strokeWidth="2" />
        {bars.length === 1 && <circle cx={geo.x(0)} cy={geo.y(bars[0].close_price)} r="5" fill={color} />}
        {hover !== null && (
          <>
            <line x1={geo.x(hover)} x2={geo.x(hover)} y1={pad.t} y2={H - pad.b} stroke="#8b95a7" strokeWidth="1" />
            <circle cx={geo.x(hover)} cy={geo.y(bars[hover].close_price)} r="4" fill={color} />
          </>
        )}
      </svg>
    </div>
  );
}
