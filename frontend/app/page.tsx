"use client";

import Link from "next/link";
import { AssetTable } from "@/components/AssetTable";
import { useAuth } from "@/components/AuthProvider";
import { Card, Empty, ErrorBox, PnL, Stat } from "@/components/ui";
import type { Account, Asset, Trade } from "@/lib/api";
import { credits, dateTime, timeAgo } from "@/lib/format";
import { useApi } from "@/lib/hooks";

type Summary = {
  popular: Asset[];
  gainers: Asset[];
  losers: Asset[];
  recent_trades: (Trade & { symbol: string | null })[];
  last_price_update_at: string | null;
  last_data_update_at: string | null;
};

export default function Home() {
  const { user } = useAuth();
  const summary = useApi<Summary>("/api/market/summary", 10_000);
  const account = useApi<Account>(user ? "/api/account" : null, 10_000);
  const notices = useApi<{ id: number; title: string; body: string; created_at: string }[]>("/api/announcements");
  const s = summary.data;
  const a = account.data;

  return (
    <div className="space-y-6">
      {notices.data && notices.data.length > 0 && (
        <div className="rounded-lg border border-sky-500/40 bg-sky-500/10 px-4 py-2 text-sm">
          📢 <b>{notices.data[0].title}</b> — {notices.data[0].body}
        </div>
      )}
      {user && a ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Stat label="총자산 (평가)" value={credits(a.total_equity)} sub={<span className="text-muted">현금 + 보유 주식 평가액</span>} />
          <Stat label="가용 현금" value={credits(a.available_cash)} sub={<span className="text-muted">예약 {credits(a.reserved_cash)}</span>} />
          <Stat label="보유 종목 수" value={a.positions_count} />
          <Stat label="실현 손익" value={<PnL value={a.realized_pnl} />} />
          <Stat label="미실현 손익" value={<PnL value={a.unrealized_pnl} />} sub={<span className="text-muted">현금에 포함되지 않음</span>} />
        </div>
      ) : !user ? (
        <Card>
          <div className="flex flex-col items-start gap-3 md:flex-row md:items-center md:justify-between">
            <div>
              <h1 className="text-lg font-bold">유튜브 채널을 가상 주식으로 거래하세요</h1>
              <p className="text-sm text-muted">가입 시 1,000,000 크레딧(현금 가치 없음) 지급 · 모든 사용자가 같은 시장 가격을 공유합니다.</p>
            </div>
            <Link href="/register" className="btn-primary">무료로 시작하기</Link>
          </div>
        </Card>
      ) : null}
      <ErrorBox error={summary.error} />

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="🔥 인기 종목 (24h 거래량)" className="lg:col-span-1">
          {s ? <AssetTable assets={s.popular} compactMode /> : <Empty>불러오는 중…</Empty>}
        </Card>
        <Card title="▲ 상승률 상위">
          {s ? <AssetTable assets={s.gainers.filter((x) => x.change_pct > 0)} compactMode /> : <Empty>…</Empty>}
        </Card>
        <Card title="▼ 하락률 상위">
          {s ? <AssetTable assets={s.losers.filter((x) => x.change_pct < 0)} compactMode /> : <Empty>…</Empty>}
        </Card>
      </div>

      <Card title="최근 체결" right={
        <span className="text-xs text-muted">
          가격 갱신 {timeAgo(s?.last_price_update_at)} · 데이터 갱신 {timeAgo(s?.last_data_update_at)}
        </span>}>
        {!s ? <Empty>…</Empty> : s.recent_trades.length === 0 ? <Empty>아직 체결이 없습니다</Empty> : (
          <div className="-mx-4 overflow-x-auto px-4">
            <table className="table">
              <thead><tr><th>시각</th><th>종목</th><th>구분</th><th className="num">가격</th><th className="num">수량</th></tr></thead>
              <tbody>
                {s.recent_trades.map((t) => (
                  <tr key={t.id}>
                    <td className="text-muted">{dateTime(t.executed_at)}</td>
                    <td><Link className="font-mono" href={`/assets/${t.asset_id}`}>{t.symbol}</Link></td>
                    <td className={t.taker_side === "buy" ? "text-up" : "text-down"}>{t.taker_side === "buy" ? "▲ 매수 체결" : "▼ 매도 체결"}</td>
                    <td className="num">{credits(t.execution_price)}</td>
                    <td className="num">{credits(t.quantity)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {s && s.gainers.length === 0 && <p className="text-sm text-muted">상장된 종목이 없습니다. 관리자가 종목을 상장하면 표시됩니다.</p>}
    </div>
  );
}
