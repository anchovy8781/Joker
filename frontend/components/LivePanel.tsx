"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { compact, credits, timeAgo } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { ChangeText } from "./AssetRow";
import { Sparkline } from "./Sparkline";

type Win = { trades: number; volume: number; value: number; buy_ratio: number | null };
export type Live = {
  server_time: string;
  asset: { id: number; symbol: string; price: number; previous_price: number; change: number; change_pct: number;
           trading_status: string; popularity_score: number | null; last_trade_at: string | null };
  game: { m1: Win; m5: Win; h1: Win; h24: Win; best_bid: number | null; best_ask: number | null; spread_bps: number | null };
  ticks: { t: string; price: number; qty: number; side: "buy" | "sell" }[];
  youtube: {
    source: "youtube_api" | "demo";
    samples: { t: string; views: number; subs: number | null }[];
    observed_at?: string; views?: number; subscribers?: number | null; videos?: number;
    views_per_hour?: number | null; subs_per_hour?: number | null;
    est_views_now?: number; est_subs_now?: number | null;
  };
  intraday?: { adj_bps: number; fair_price: number; reference_price: number; updated_at: string | null;
               detail: { yt_bps: number; subs_bps: number; flow_bps: number; views_per_hour: number | null;
                         views_per_hour_7d: number | null } | null };
};

const bp = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${(Math.abs(v) / 100).toFixed(2)}%`;

function FairValue({ i }: { i: NonNullable<Live["intraday"]> }) {
  const d = i.detail;
  const parts: [string, number][] = d ? [["유튜브 조회 속도", d.yt_bps], ["구독자 증가세", d.subs_bps], ["게임 매수세", d.flow_bps]] : [];
  return (
    <div className="rounded-xl bg-panel2 px-3 py-2.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs text-muted" title="하루 기준가에 실시간 지표를 반영해 5분마다 다시 계산한 값. 시장조성자의 호가가 이 가격을 중심으로 움직여요.">장중 적정가 ⓘ</span>
        <span className="text-xs text-muted">기준가 ₩{credits(i.reference_price)}</span>
      </div>
      <div className="mt-0.5 flex items-baseline gap-2">
        <span className="text-lg font-bold tabular-nums">₩{credits(i.fair_price)}</span>
        <span className={`text-sm font-semibold ${i.adj_bps > 0 ? "text-up" : i.adj_bps < 0 ? "text-down" : "text-muted"}`}>{bp(i.adj_bps)}</span>
      </div>
      {parts.length > 0 && (
        <div className="mt-1.5 grid grid-cols-3 gap-1 text-center text-xs">
          {parts.map(([label, v]) => (
            <div key={label} className="rounded-lg bg-bg/60 px-1 py-1">
              <div className="text-muted">{label}</div>
              <div className={v > 0 ? "text-up" : v < 0 ? "text-down" : "text-muted"}>{bp(v)}</div>
            </div>
          ))}
        </div>
      )}
      {d?.views_per_hour != null && d.views_per_hour_7d != null && d.views_per_hour_7d > 0 && (
        <div className="mt-1 text-xs text-muted">지금 조회 속도는 평소(7일 평균)의 {Math.round((d.views_per_hour / d.views_per_hour_7d) * 100)}%</div>
      )}
    </div>
  );
}

const POLL_MS = 2_000;
const KEEP_POINTS = 180; // 6 minutes of 2-second points

function Tile({ label, value, sub, hint }: { label: string; value: React.ReactNode; sub?: React.ReactNode; hint?: string }) {
  return (
    <div className="rounded-xl bg-panel2 px-3 py-2.5" title={hint}>
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-0.5 text-lg font-bold tabular-nums">{value}</div>
      {sub && <div className="text-xs text-muted">{sub}</div>}
    </div>
  );
}

/** Price line from 2-second polls (plus recent trades) with per-trade volume bars underneath. */
function LiveChart({ points, ticks, up }: { points: { t: number; p: number }[]; ticks: Live["ticks"]; up: boolean }) {
  const W = 800, H = 220, VOL = 46, pad = 6;
  if (points.length < 2) return <div className="flex h-[180px] items-center justify-center text-sm text-muted">실시간 데이터를 모으는 중…</div>;
  const t0 = points[0].t, t1 = points[points.length - 1].t;
  const prices = points.map((x) => x.p);
  let min = Math.min(...prices), max = Math.max(...prices);
  if (min === max) { min *= 0.998; max *= 1.002; }
  const x = (t: number) => pad + ((t - t0) / Math.max(1, t1 - t0)) * (W - 2 * pad);
  const y = (p: number) => pad + (1 - (p - min) / (max - min)) * (H - VOL - 2 * pad);
  const d = points.map((q, i) => `${i ? "L" : "M"}${x(q.t).toFixed(1)},${y(q.p).toFixed(1)}`).join(" ");
  const color = up ? "#f04452" : "#3d8bfd";
  const inRange = ticks.filter((k) => Date.parse(k.t) >= t0);
  const maxQty = Math.max(1, ...inRange.map((k) => k.qty));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label="실시간 가격·체결량 차트">
      <defs>
        <linearGradient id="livefill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.25" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${d} L${x(t1)},${H - VOL} L${x(t0)},${H - VOL} Z`} fill="url(#livefill)" />
      <path d={d} fill="none" stroke={color} strokeWidth="2.2" strokeLinejoin="round" />
      <circle cx={x(t1)} cy={y(prices[prices.length - 1])} r="5" fill={color}>
        <animate attributeName="r" values="4;7;4" dur="2s" repeatCount="indefinite" />
      </circle>
      <text x={W - pad} y={y(max) + 12} textAnchor="end" fill="#8e94a3" fontSize="12">{credits(max)}</text>
      <text x={W - pad} y={y(min) - 4} textAnchor="end" fill="#8e94a3" fontSize="12">{credits(min)}</text>
      {inRange.map((k, i) => {
        const h = Math.max(2, (k.qty / maxQty) * (VOL - 8));
        return <rect key={i} x={x(Date.parse(k.t)) - 2} y={H - h} width="4" height={h} rx="1"
          fill={k.side === "buy" ? "#f04452" : "#3d8bfd"} opacity="0.85" />;
      })}
    </svg>
  );
}

export function LivePanel({ assetId }: { assetId: number }) {
  const live = useApi<Live>(`/api/assets/${assetId}/live`, POLL_MS);
  const [points, setPoints] = useState<{ t: number; p: number }[]>([]);
  const seeded = useRef(false);
  const L = live.data;

  useEffect(() => {
    if (!L) return;
    const now = Date.parse(L.server_time);
    setPoints((cur) => {
      let base = cur;
      if (!seeded.current) {
        seeded.current = true;
        base = L.ticks.filter((k) => now - Date.parse(k.t) < KEEP_POINTS * POLL_MS)
          .map((k) => ({ t: Date.parse(k.t), p: k.price }));
      }
      const next = [...base, { t: now, p: L.asset.price }].filter((q, i, arr) => i === 0 || q.t > arr[i - 1].t);
      return next.slice(-KEEP_POINTS);
    });
  }, [L]);

  const yt = L?.youtube;
  const viewsSpark = useMemo(() => (yt?.samples ?? []).map((s) => s.views), [yt?.samples]);
  const subsSpark = useMemo(() => (yt?.samples ?? []).filter((s) => s.subs !== null).map((s) => s.subs as number), [yt?.samples]);
  if (!L) return <div className="rounded-2xl bg-panel p-6 text-center text-sm text-muted">{live.error ? "실시간 데이터를 불러오지 못했어요" : "실시간 연결 중…"}</div>;

  const g = L.game;
  const buyPct = g.m5.buy_ratio === null ? null : Math.round(g.m5.buy_ratio * 100);
  return (
    <section className="space-y-3 rounded-2xl bg-panel p-4" aria-live="off">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-base font-bold">
          <span className="relative flex h-2.5 w-2.5"><span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-up opacity-60" /><span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-up" /></span>
          실시간
        </h2>
        <span className="text-xs text-muted">2초마다 갱신</span>
      </div>
      <div className="flex items-baseline gap-2">
        <span className="text-3xl font-bold tabular-nums">₩{credits(L.asset.price)}</span>
        <ChangeText percent={L.asset.change_pct} />
      </div>
      <LiveChart points={points} ticks={L.ticks} up={L.asset.change >= 0} />

      <h3 className="pt-1 text-sm font-bold text-muted">게임 내 거래</h3>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Tile label="1분 거래량" value={`${credits(g.m1.volume)}주`} sub={`체결 ${g.m1.trades}건`} />
        <Tile label="1분 거래대금" value={`₩${compact(g.m1.value)}`} sub={`5분 ₩${compact(g.m5.value)}`} />
        <Tile label="1시간 거래대금" value={`₩${compact(g.h1.value)}`} sub={`24시간 ₩${compact(g.h24.value)}`} />
        <Tile label="매수 비중 (5분)" hint="최근 5분 체결 중 매수 주문이 먼저 들어온 비율"
          value={buyPct === null ? "—" : <span className={buyPct >= 50 ? "text-up" : "text-down"}>{buyPct}%</span>}
          sub={buyPct === null ? "체결 없음" : (
            <span className="mt-1 flex h-1.5 overflow-hidden rounded-full bg-down/60"><span className="bg-up" style={{ width: `${buyPct}%` }} /></span>
          )} />
        <Tile label="최우선 매수호가" value={g.best_bid ? `₩${credits(g.best_bid)}` : "—"} />
        <Tile label="최우선 매도호가" value={g.best_ask ? `₩${credits(g.best_ask)}` : "—"} sub={g.spread_bps !== null ? `스프레드 ${(g.spread_bps / 100).toFixed(2)}%` : undefined} />
        <Tile label="24시간 거래량" value={`${credits(g.h24.volume)}주`} sub={`체결 ${credits(g.h24.trades)}건`} />
        <Tile label="인기도" value={L.asset.popularity_score ? `${L.asset.popularity_score} / 50` : "—"} sub={`마지막 체결 ${timeAgo(L.asset.last_trade_at)}`} />
      </div>

      {L.intraday && <FairValue i={L.intraday} />}

      <h3 className="pt-1 text-sm font-bold text-muted">유튜브 채널 {yt?.source === "demo" && <span className="ml-1 rounded bg-accent/15 px-1 text-[11px] text-accent">DEMO 데이터</span>}</h3>
      {yt?.views === undefined ? (
        <p className="rounded-xl bg-panel2 px-3 py-3 text-sm text-muted">채널 측정값을 모으는 중이에요 (5분마다 측정).</p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Tile label="조회수 증가 속도" value={yt.views_per_hour == null ? "—" : `${compact(Math.max(0, yt.views_per_hour))}/시간`}
              sub={yt.views_per_hour == null ? "측정 2회 이상 필요" : `분당 약 ${compact(Math.max(0, yt.views_per_hour) / 60)}회`} />
            <Tile label="구독자 증가" value={yt.subs_per_hour == null ? "—" : `${yt.subs_per_hour >= 0 ? "+" : ""}${compact(yt.subs_per_hour * 24)}/일`}
              sub={yt.subs_per_hour == null ? "측정 2회 이상 필요" : `시간당 ${yt.subs_per_hour >= 0 ? "+" : ""}${compact(yt.subs_per_hour)}`} />
            <Tile label="실시간 조회수 (추정)" hint="마지막 측정값 + 최근 증가 속도로 계산한 추정치"
              value={<span className="text-base sm:text-lg">{credits(yt.est_views_now)}</span>} sub={`측정값 ${credits(yt.views)}`} />
            <Tile label="구독자 (추정)" value={yt.subscribers == null ? "비공개" : <span className="text-base sm:text-lg">{credits(yt.est_subs_now ?? yt.subscribers)}</span>}
              sub={`영상 ${credits(yt.videos)}개`} />
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="rounded-xl bg-panel2 px-3 py-2">
              <div className="text-xs text-muted">누적 조회수 · 24시간</div>
              <Sparkline points={viewsSpark} width={360} height={48} className="w-full" />
            </div>
            <div className="rounded-xl bg-panel2 px-3 py-2">
              <div className="text-xs text-muted">구독자 · 24시간</div>
              <Sparkline points={subsSpark} width={360} height={48} className="w-full" />
            </div>
          </div>
          <p className="text-xs leading-relaxed text-muted">
            게임 내 거래 수치는 실제 체결 기준이에요. 유튜브 수치는 무료 API 한도 때문에 5분마다 측정하며
            (마지막 측정 {timeAgo(yt.observed_at)}), &lsquo;추정&rsquo;은 최근 증가 속도로 계산한 값이에요.
          </p>
        </>
      )}
    </section>
  );
}
