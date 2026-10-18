"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { OrderBookView } from "@/components/OrderBookView";
import { OrderPanel } from "@/components/OrderPanel";
import { AutoOrderPanel } from "@/components/AutoOrderPanel";
import { ChatBox } from "@/components/ChatBox";
import { useMarketHours } from "@/components/MarketHoursBadge";
import { CandleChart, type VolumeProfile } from "@/components/CandleChart";
import { ChannelAvatar } from "@/components/ChannelAvatar";
import { LivePanel } from "@/components/LivePanel";
import { PriceAlerts } from "@/components/PriceAlerts";
import { Card, Change, DemoBadge, Empty, ErrorBox, FreshnessBadge, TradingBadge } from "@/components/ui";
import type { Account, Asset, Bar, MetricPoint, Order, OrderBook, Position, Trade } from "@/lib/api";
import { api } from "@/lib/api";
import { compact, credits, dateTime, orderStatusLabel, pct, popularityLabel } from "@/lib/format";
import { usePriceFlash } from "@/lib/feedback";
import { useApi } from "@/lib/hooks";

type PriceUpdate = { effective_date: string; status: string; old_price: number; new_price: number; daily_return: string | null; fundamental_return: string | null; flow_return: string | null; sentiment_return: string | null; error: string | null };
type Event = { id: number; event_type: string; event_score: number; source: string; observed_at: string; expires_at: string; active: boolean };

const EVENT_LABEL: Record<string, string> = {
  trending: "인기 급상승 (YouTube 인기 차트)",
  growth_acceleration: "구독자 성장 가속",
  subscriber_loss: "구독자 감소",
  sustained_view_decline: "조회 수 증가세 지속 둔화",
};

function growth(points: MetricPoint[], key: "subscriber_count" | "view_count", days: number): number | null {
  const valid = points.filter((p) => p[key] !== null);
  if (valid.length < 2) return null;
  const last = valid[valid.length - 1];
  const target = new Date(last.observed_date);
  target.setDate(target.getDate() - days);
  const base = [...valid].reverse().find((p) => new Date(p.observed_date) <= target);
  if (!base || !base[key]) return null;
  return ((last[key]! - base[key]!) / base[key]!) * 100;
}

type ChartInterval = "1s" | "5s" | "10s" | "30s" | "1m" | "3m" | "5m" | "15m" | "30m" | "1h" | "1d" | "1w" | "1mo";
const INTERVALS: { key: ChartInterval; label: string; group: string; limit: number; poll: number }[] = [
  { key: "1s", label: "1초", group: "초", limit: 300, poll: 3_000 },
  { key: "5s", label: "5초", group: "초", limit: 300, poll: 3_000 },
  { key: "10s", label: "10초", group: "초", limit: 300, poll: 3_000 },
  { key: "30s", label: "30초", group: "초", limit: 300, poll: 5_000 },
  { key: "1m", label: "1분", group: "분", limit: 300, poll: 5_000 },
  { key: "3m", label: "3분", group: "분", limit: 300, poll: 10_000 },
  { key: "5m", label: "5분", group: "분", limit: 300, poll: 10_000 },
  { key: "15m", label: "15분", group: "분", limit: 90, poll: 15_000 },
  { key: "30m", label: "30분", group: "분", limit: 90, poll: 30_000 },
  { key: "1h", label: "시간", group: "시간", limit: 120, poll: 30_000 },
  { key: "1d", label: "일", group: "일", limit: 120, poll: 60_000 },
  { key: "1w", label: "주", group: "주", limit: 104, poll: 60_000 },
  { key: "1mo", label: "월", group: "월", limit: 60, poll: 60_000 },
];
const GROUPS = ["초", "분", "시간", "일", "주", "월"] as const;

export default function AssetPage() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const [chartInterval, setChartInterval] = useState<ChartInterval>("1m");
  const [show, setShow] = useState({ ma: true, trend: true, profile: true });
  const [picked, setPicked] = useState<number | null>(null);
  const asset = useApi<Asset>(`/api/assets/${id}`, 4_000);
  const iv = INTERVALS.find((x) => x.key === chartInterval)!;
  const bars = useApi<Bar[]>(`/api/assets/${id}/history?interval=${chartInterval}&limit=${iv.limit}`, iv.poll);
  const since = bars.data?.[0]?.timestamp;
  const profile = useApi<VolumeProfile>(show.profile && since ? `/api/assets/${id}/volume-profile?bins=24&since=${encodeURIComponent(since)}` : null, 30_000);
  const book = useApi<OrderBook>(`/api/assets/${id}/orderbook`, 4_000);
  const trades = useApi<Trade[]>(`/api/assets/${id}/trades?limit=30`, 8_000);
  const metrics = useApi<MetricPoint[]>(`/api/assets/${id}/metrics?days=60`);
  const events = useApi<Event[]>(`/api/assets/${id}/events`);
  const updates = useApi<PriceUpdate[]>(`/api/assets/${id}/price-updates`);
  const account = useApi<Account>(user ? "/api/account" : null, 10_000);
  const hours = useMarketHours();
  const [panel, setPanel] = useState<"order" | "auto" | "alert">("order");
  const [sideReq, setSideReq] = useState<{ side: "buy" | "sell"; n: number } | undefined>();
  const positions = useApi<Position[]>(user ? "/api/account/positions" : null, 10_000);
  const orders = useApi<Order[]>(user ? `/api/orders?asset_id=${id}&limit=20` : null, 10_000);

  const a = asset.data;
  if (asset.error) return <ErrorBox error={asset.error} />;
  if (!a) return <Empty>불러오는 중…</Empty>;
  const pos = positions.data?.find((p) => p.asset_id === a.id);
  const m = metrics.data ?? [];
  const latest = m[m.length - 1];
  const refresh = () => { asset.reload(); book.reload(); trades.reload(); account.reload(); positions.reload(); orders.reload(); };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <ChannelAvatar src={a.channel.thumbnail_url} title={a.channel.title} seed={a.symbol} size={64} />
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-xl font-bold">{a.channel.title.replace(/^\[DEMO\]\s*/, "")}</h1>
          <div className="flex flex-wrap items-center gap-1.5 text-sm text-muted">
            <span className="font-mono">{a.symbol}</span>
            {a.channel.handle && <span>· {a.channel.handle}</span>}
            <DemoBadge isDemo={a.is_demo} />
            <TradingBadge status={a.trading_status} reason={a.halt_reason} />
            <FreshnessBadge f={a.freshness} />
          </div>
        </div>
      </div>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <PriceTicker price={a.current_price} />
        <Change value={a.change} percent={a.change_pct} />
        <span className="text-xs text-muted">기준가 {credits(a.reference_price)} · 전일 {credits(a.previous_price)} · 인기도 {a.popularity_score ?? "—"}/50 {popularityLabel(a.popularity_score)}</span>
      </div>
      <div className="flex gap-2 lg:hidden">
        <a href="#order-card" onClick={() => { setPanel("order"); setSideReq({ side: "buy", n: Date.now() }); }}
          className="flex-1 rounded-full bg-up py-3 text-center font-bold text-white">매수</a>
        <a href="#order-card" onClick={() => { setPanel("order"); setSideReq({ side: "sell", n: Date.now() }); }}
          className="flex-1 rounded-full bg-down py-3 text-center font-bold text-white">매도</a>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Metric label="시가총액" value={`${compact(a.market_cap)}`} />
        <Metric label="거래대금 (24h)" value={`${compact(a.turnover_24h)}`} />
        <Metric label="거래량 (24h)" value={`${credits(a.volume_24h)}주`} />
        <Metric label="인기도" value={a.popularity_score ? `${a.popularity_score} / 50` : "—"} />
      </div>
      {a.fee_surcharge_reason && (
        <div className="rounded-xl border border-orange-500/50 bg-orange-500/10 px-4 py-2 text-sm text-orange-200">
          🔥 {a.fee_surcharge_reason === "new_video" ? "신규 영상 업로드" : "유명인 신규 채널"} 할증 수수료 {a.fee_bps / 100}% 적용 중
          {a.fee_surcharge_until && ` (${dateTime(a.fee_surcharge_until)}까지)`} · 손실 매도 시 매도 수수료 면제
        </div>
      )}
      {a.trading_status !== "active" && (
        <div role="alert" className="rounded-xl border border-red-500/50 bg-red-500/10 px-4 py-2 text-sm text-red-200">
          ⏸ 거래 정지: {a.halt_reason?.replace(/^AUTO:/, "자동 정지 · ")}
        </div>
      )}
      {a.freshness.status !== "fresh" && (
        <div className="rounded-xl border border-amber-500/50 bg-amber-500/10 px-4 py-2 text-sm text-amber-200">
          ⚠ 채널 데이터가 최신이 아닙니다 (마지막 갱신: {dateTime(a.freshness.last_update_at)}). 표시된 지표는 최신 값이 아닐 수 있습니다.
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="space-y-4">
          <LivePanel assetId={a.id} />
          <Card title="차트">
            <div className="mb-2 flex gap-1.5" role="tablist" aria-label="차트 종류">
              {GROUPS.map((g) => (
                <button key={g} role="tab" aria-selected={iv.group === g}
                  onClick={() => setChartInterval(INTERVALS.find((x) => x.group === g)!.key)}
                  className={`chip flex-1 px-2 py-1.5 text-sm ${iv.group === g ? "chip-on" : ""}`}>{g === "초" || g === "분" ? `${g}봉` : g}</button>
              ))}
            </div>
            {(iv.group === "초" || iv.group === "분") && (
              <div className="mb-2 flex gap-1.5" role="tablist" aria-label={`${iv.group}봉 간격`}>
                {INTERVALS.filter((x) => x.group === iv.group).map((x) => (
                  <button key={x.key} role="tab" aria-selected={chartInterval === x.key} onClick={() => setChartInterval(x.key)}
                    className={`flex-1 rounded-full border px-2 py-1 text-xs font-semibold ${chartInterval === x.key ? "border-text text-text" : "border-line text-muted"}`}>{x.label}</button>
                ))}
              </div>
            )}
            <div className="mb-3 flex flex-wrap gap-1.5">
              {([["ma", "이동평균선"], ["trend", "추세선"], ["profile", "매물대"]] as const).map(([k, label]) => (
                <button key={k} aria-pressed={show[k]} onClick={() => setShow({ ...show, [k]: !show[k] })}
                  className={`rounded-full border px-3 py-1 text-xs font-semibold ${show[k] ? "border-text text-text" : "border-line text-muted"}`}>
                  {show[k] ? "✓ " : ""}{label}
                </button>
              ))}
            </div>
            <CandleChart bars={bars.data ?? []} interval={chartInterval} profile={show.profile ? profile.data : null} show={show} />
          </Card>

          <Card title="채널 공개 지표" right={<span className="text-xs text-muted">{a.is_demo ? "데모 공급자 생성값" : "YouTube Data API 공개 통계"}</span>}>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Metric label="구독자" value={a.channel.hidden_subscriber_count ? "비공개" : compact(latest?.subscriber_count)} />
              <Metric label="총 조회 수" value={compact(latest?.view_count)} />
              <Metric label="구독자 7일 변화" value={fmtGrowth(growth(m, "subscriber_count", 7))} />
              <Metric label="조회 수 7일 / 28일 증가" value={`${fmtGrowth(growth(m, "view_count", 7))} / ${fmtGrowth(growth(m, "view_count", 28))}`} />
            </div>
            <p className="mt-3 text-xs text-muted">기간별 변화는 이 서비스가 직접 수집한 일별 기록으로 계산합니다. 데이터 품질: {a.channel.data_quality_status}</p>
          </Card>

          <div className="grid gap-4 md:grid-cols-2">
            <Card title="시장 이벤트 (검증된 데이터 기반)">
              {!events.data?.length ? <Empty>이벤트 없음</Empty> : (
                <ul className="space-y-2 text-sm">
                  {events.data.slice(0, 8).map((e) => (
                    <li key={e.id} className="flex items-center justify-between gap-2">
                      <span>{e.event_score > 0 ? "▲" : "▼"} {EVENT_LABEL[e.event_type] ?? e.event_type}</span>
                      <span className={`text-xs ${e.active ? "text-text" : "text-muted line-through"}`}>{e.active ? "반영 중" : "만료"} · {dateTime(e.observed_at)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
            <Card title="일일 가격 산정 내역">
              {!updates.data?.length ? <Empty>아직 산정 기록이 없습니다</Empty> : (
                <table className="table text-xs">
                  <thead><tr><th>날짜</th><th className="num">이전 → 신규</th><th className="num">변동</th><th>상태</th></tr></thead>
                  <tbody>
                    {updates.data.slice(0, 7).map((u) => (
                      <tr key={u.effective_date} title={`실적 ${u.fundamental_return ?? "-"} / 수급 ${u.flow_return ?? "-"} / 심리 ${u.sentiment_return ?? "-"}`}>
                        <td>{u.effective_date}</td>
                        <td className="num">{credits(u.old_price)} → {credits(u.new_price)}</td>
                        <td className="num">{u.daily_return ? pct(Number(u.daily_return) * 100) : "—"}</td>
                        <td>{u.status === "applied" ? "적용" : u.status === "skipped" ? "보류(데이터)" : "실패·유지"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>
          </div>
        </div>

        <div className="space-y-4">
          <Card className="scroll-mt-20" id="order-card" title={
            <div id="order" className="flex gap-1">
              <button onClick={() => setPanel("order")} className={`rounded-full px-2 py-0.5 ${panel === "order" ? "bg-panel2" : "text-muted"}`}>주문</button>
              <button onClick={() => setPanel("auto")} className={`rounded-full px-2 py-0.5 ${panel === "auto" ? "bg-panel2" : "text-muted"}`}>자동 매매</button>
              <button onClick={() => setPanel("alert")} className={`rounded-full px-2 py-0.5 ${panel === "alert" ? "bg-panel2" : "text-muted"}`}>🔔 알림</button>
            </div>}>
            {panel === "alert" ? (user ? <PriceAlerts assetId={a.id} current={a.current_price} /> : <p className="text-sm text-muted">로그인 후 이용하세요.</p>)
              : panel === "auto" ? (user ? <AutoOrderPanel asset={a} /> : <p className="text-sm text-muted">로그인 후 이용하세요.</p>) : <>
            <OrderPanel asset={a} loggedIn={!!user} pickedPrice={picked} marketOpen={hours.data?.open ?? true} requestedSide={sideReq}
              availableCash={account.data?.available_cash ?? null}
              availableShares={pos ? pos.quantity - pos.reserved_quantity : user ? 0 : null}
              onDone={refresh} />
            {pos && pos.quantity > 0 && (
              <div className="mt-3 rounded-xl bg-panel2 p-3 text-xs text-muted">
                보유 {credits(pos.quantity)}주 · 평균 {credits(pos.average_cost)} · 평가 {credits(pos.market_value)}
              </div>
            )}
            </>}
          </Card>
          <Card title="호가창"><OrderBookView book={book.data} onPick={(p) => { setPicked(p); setPanel("order"); document.getElementById("order")?.scrollIntoView({ behavior: "smooth", block: "start" }); }}
            currentPrice={a.current_price} previousPrice={a.previous_price} /></Card>
          <Card title={`💬 ${a.symbol} 종목방`}><ChatBox room={`asset:${a.id}`} height="h-64" /></Card>
          <Card title="최근 체결">
            {!trades.data?.length ? <Empty>체결 없음</Empty> : (
              <table className="table text-xs">
                <thead><tr><th>시각</th><th className="num">가격</th><th className="num">수량</th></tr></thead>
                <tbody>
                  {trades.data.map((t) => (
                    <tr key={t.id}>
                      <td className="text-muted">{new Date(t.executed_at).toLocaleTimeString("ko-KR")}</td>
                      <td className={`num ${t.taker_side === "buy" ? "text-up" : "text-down"}`}>{t.taker_side === "buy" ? "▲" : "▼"} {credits(t.execution_price)}</td>
                      <td className="num">{credits(t.quantity)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
          {user && (
            <Card title="내 주문 (이 종목)">
              {!orders.data?.length ? <Empty>주문 없음</Empty> : (
                <ul className="space-y-2 text-xs">
                  {orders.data.map((o) => (
                    <li key={o.id} className="flex items-center justify-between gap-2">
                      <span className={o.side === "buy" ? "text-up" : "text-down"}>{o.side === "buy" ? "▲ 매수" : "▼ 매도"} {o.order_type === "limit" ? credits(o.limit_price) : "시장가"}</span>
                      <span className="text-muted">{o.filled_quantity}/{o.quantity} · {orderStatusLabel(o.status)}</span>
                      {(o.status === "open" || o.status === "partially_filled" || o.status === "scheduled") && (
                        <button className="btn-ghost px-2 py-0.5 text-xs" onClick={async () => { await api(`/api/orders/${o.id}/cancel`, { method: "POST" }); refresh(); }}>취소</button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function fmtGrowth(v: number | null) {
  return v === null ? "기록 부족" : pct(v);
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-panel2 p-3">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-1 font-mono text-base tabular-nums">{value}</div>
    </div>
  );
}

/** The big price, flashing red/blue on each tick like a trading screen. */
function PriceTicker({ price }: { price: number }) {
  const flash = usePriceFlash(price);
  return <span className={`px-1 text-3xl font-bold tabular-nums ${flash}`}>₩{credits(price)}</span>;
}
