"use client";

import Link from "next/link";
import { useAuth } from "@/components/AuthProvider";
import { ChangeText, channelName } from "@/components/AssetRow";
import { ChannelAvatar } from "@/components/ChannelAvatar";
import { Sparkline } from "@/components/Sparkline";
import { TierBadge } from "@/components/TierBadge";
import { Empty, ErrorBox, PnL } from "@/components/ui";
import type { Account, Asset } from "@/lib/api";
import { compact, credits, timeAgo } from "@/lib/format";
import { useApi } from "@/lib/hooks";

function Section({ title, children, right }: { title: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <section className="min-w-0 rounded-3xl bg-panel p-5">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-bold">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  );
}

function MoverCard({ a }: { a: Asset }) {
  return (
    <Link href={`/assets/${a.id}`} className="w-44 shrink-0 snap-start rounded-3xl bg-panel p-4 sm:w-52">
      <ChannelAvatar src={a.channel.thumbnail_url} title={a.channel.title} seed={a.symbol} size={64} />
      <div className="mt-3 truncate text-base font-bold">{channelName(a)}</div>
      <div className="truncate font-mono text-sm text-muted">{a.symbol}</div>
      <Sparkline points={a.sparkline} width={160} height={44} className="my-2 w-full" />
      <div className="flex items-baseline justify-between gap-1">
        <span className="min-w-0 truncate font-bold tabular-nums">₩{credits(a.current_price)}</span>
        <ChangeText percent={a.change_pct} className="text-sm" />
      </div>
    </Link>
  );
}

export default function Home() {
  const { user } = useAuth();
  const assets = useApi<Asset[]>("/api/assets", 15_000);
  const account = useApi<Account>(user ? "/api/account" : null, 15_000);
  const notices = useApi<{ id: number; title: string; body: string; created_at: string }[]>("/api/announcements");
  const list = assets.data ?? [];
  const movers = [...list].filter((a) => a.change_pct > 0).sort((x, y) => y.change_pct - x.change_pct).slice(0, 5);
  const byTurnover = [...list].sort((x, y) => y.turnover_24h - x.turnover_24h).slice(0, 3);
  const byCap = [...list].sort((x, y) => y.market_cap - x.market_cap).slice(0, 3);
  const newest = [...list].filter((a) => a.channel.listed_at)
    .sort((x, y) => Date.parse(y.channel.listed_at!) - Date.parse(x.channel.listed_at!)).slice(0, 3);
  const a = account.data;

  return (
    <div className="space-y-5">
      {notices.data && notices.data.length > 0 && (
        <div className="rounded-2xl bg-sky-500/10 px-4 py-3 text-sm [overflow-wrap:anywhere]">📢 <b>{notices.data[0].title}</b> — {notices.data[0].body}</div>
      )}

      {user && a ? (
        <Link href="/portfolio" className="block rounded-3xl bg-panel p-5">
          <div className="flex items-center justify-between text-sm text-muted">
            <span>내 총자산</span><span aria-hidden>›</span>
          </div>
          <div className="mt-1 text-[28px] font-bold leading-tight tabular-nums">{credits(a.total_equity)}원</div>
          <div className="mt-1 text-sm">평가손익 <PnL value={a.unrealized_pnl} /></div>
          {a.level !== undefined && (
            <div className="mt-4">
              <div className="flex items-center justify-between text-sm">
                <span className="flex items-center gap-2 font-bold">Lv.{a.level} <TierBadge tier={a.tier} /></span>
                <span className="text-xs text-muted tabular-nums">{credits(a.xp - a.level_start_xp)} / {credits(a.next_level_xp - a.level_start_xp)} XP</span>
              </div>
              <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-panel2" role="progressbar" aria-label="다음 레벨까지"
                aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(((a.xp - a.level_start_xp) / Math.max(1, a.next_level_xp - a.level_start_xp)) * 100)}>
                <div className="h-full rounded-full bg-primary transition-all"
                  style={{ width: `${Math.min(100, ((a.xp - a.level_start_xp) / Math.max(1, a.next_level_xp - a.level_start_xp)) * 100)}%` }} />
              </div>
              <p className="mt-1 text-xs text-muted">
                체결·거래대금·미션으로 XP를 모아요{a.next_tier_equity ? ` · 총자산 ${credits(a.next_tier_equity)}원이면 다음 등급` : " · 최고 등급"}
              </p>
            </div>
          )}
          <div className="mt-4 flex items-center justify-between rounded-2xl bg-panel2 px-4 py-3 text-sm">
            <span className="text-muted">주문 가능 금액</span>
            <b className="tabular-nums">{credits(a.available_cash)}원</b>
          </div>
        </Link>
      ) : !user ? (
        <div className="rounded-3xl bg-panel p-5">
          <h1 className="text-2xl font-bold leading-snug">유튜브 채널을<br />주식처럼 사고팔아요</h1>
          <p className="mt-1 text-sm text-muted">가입하면 가상 크레딧 1,000,000을 무료로 드려요. 미션으로 더 모을 수 있어요. (현금 가치 없음)</p>
          <Link href="/register" className="btn-primary mt-5 block py-3.5 text-center text-base">무료로 시작하기</Link>
        </div>
      ) : null}
      <ErrorBox error={assets.error} />

      <section>
        <h2 className="mb-3 px-1 text-xl font-bold">오늘 상승률 TOP 5</h2>
        {!assets.data ? <Empty>불러오는 중…</Empty> : movers.length === 0 ? (
          <div className="rounded-2xl bg-panel"><Empty>오늘 오른 종목이 아직 없어요</Empty></div>
        ) : (
          <div className="no-scrollbar -mx-4 flex snap-x gap-3 overflow-x-auto px-4 pb-1">
            {movers.map((m) => <MoverCard key={m.id} a={m} />)}
          </div>
        )}
      </section>

      <h2 className="px-1 pt-2 text-xl font-bold">한눈에 보기</h2>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Section title="24시간 거래대금 TOP 3">
          {byTurnover.length === 0 ? <Empty>…</Empty> : (
            <ol className="space-y-4">
              {byTurnover.map((x, i) => (
                <li key={x.id}>
                  <Link href={`/assets/${x.id}`} className="flex items-center gap-3">
                    <span className="w-4 text-lg font-bold italic text-muted">{i + 1}</span>
                    <ChannelAvatar src={x.channel.thumbnail_url} title={x.channel.title} seed={x.symbol} size={48} />
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-bold">{channelName(x)}</div>
                      <div className="font-mono text-sm text-muted">{x.symbol}</div>
                    </div>
                    <div className="text-right">
                      <div className="font-bold tabular-nums">₩{compact(x.turnover_24h)}</div>
                      <ChangeText percent={x.change_pct} className="text-sm" />
                    </div>
                  </Link>
                </li>
              ))}
            </ol>
          )}
        </Section>

        <Section title="신규 상장" right={<Link href="/listings" className="text-sm text-muted">상장 신청 ›</Link>}>
          {newest.length === 0 ? <Empty>아직 상장된 종목이 없어요</Empty> : (
            <ul className="space-y-4">
              {newest.map((x) => (
                <li key={x.id} className="flex items-center gap-3">
                  <ChannelAvatar src={x.channel.thumbnail_url} title={x.channel.title} seed={x.symbol} size={48} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs font-bold text-emerald-400">NEW</span>
                      <span className="truncate font-bold">{channelName(x)}</span>
                    </div>
                    <div className="truncate text-sm text-muted">{x.symbol} · {timeAgo(x.channel.listed_at)} · <ChangeText percent={x.change_pct} /></div>
                  </div>
                  <div className="text-right">
                    <div className="text-sm font-bold tabular-nums">₩{credits(x.current_price)}</div>
                    <Link href={`/assets/${x.id}`} className="mt-1 inline-block rounded-lg bg-primary/15 px-3 py-1.5 text-xs font-bold text-primary">사러가기</Link>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section title="시가총액 TOP 3">
          {byCap.length === 0 ? <Empty>…</Empty> : (
            <ol className="space-y-4">
              {byCap.map((x, i) => (
                <li key={x.id}>
                  <Link href={`/assets/${x.id}`} className="flex items-center gap-3">
                    <span className="w-4 text-lg font-bold italic text-muted">{i + 1}</span>
                    <ChannelAvatar src={x.channel.thumbnail_url} title={x.channel.title} seed={x.symbol} size={48} />
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-bold">{channelName(x)}</div>
                      <div className="font-mono text-sm text-muted">{x.symbol}</div>
                    </div>
                    <div className="text-right font-bold tabular-nums">₩{compact(x.market_cap)}</div>
                  </Link>
                </li>
              ))}
            </ol>
          )}
        </Section>
      </div>
      {assets.data && list.length === 0 && <p className="text-sm text-muted">상장된 종목이 없습니다. 관리자가 종목을 상장하면 표시됩니다.</p>}
    </div>
  );
}
