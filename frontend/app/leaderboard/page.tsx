"use client";

import { useAuth } from "@/components/AuthProvider";
import { TierBadge } from "@/components/TierBadge";
import { Change, Empty, ErrorBox } from "@/components/ui";
import type { LeaderRow } from "@/lib/api";
import { compact, credits } from "@/lib/format";
import { useApi } from "@/lib/hooks";

export default function Leaderboard() {
  const { user } = useAuth();
  const { data, error } = useApi<LeaderRow[]>("/api/leaderboard?limit=100", 30_000);
  const mine = data?.find((r) => r.username === user?.username);
  return (
    <div className="space-y-3">
      <div>
        <h1 className="text-2xl font-bold">자산 랭킹</h1>
        <p className="text-sm text-muted">현금 + 보유 주식(현재가) · 시작 자금 1,000,000 대비 수익률</p>
      </div>
      <ErrorBox error={error} />
      {mine && (
        <div className="flex items-center justify-between rounded-3xl bg-primary/15 px-4 py-3">
          <span className="font-bold">내 순위 {mine.rank}위 <TierBadge tier={mine.tier} className="ml-1" /></span>
          <span className="text-right"><b className="tabular-nums">₩{credits(mine.total_equity)}</b> <Change percent={mine.return_pct} className="text-sm" /></span>
        </div>
      )}
      {!data ? <Empty>불러오는 중…</Empty> : data.length === 0 ? <Empty>아직 참가자가 없어요</Empty> : (
        <ol className="space-y-2">
          {data.map((r) => (
            <li key={r.username} className={`flex items-center gap-3 rounded-3xl px-4 py-3 ${r.username === user?.username ? "bg-panel2" : "bg-panel"}`}>
              <span className="w-8 text-center text-lg font-bold">{r.rank <= 3 ? ["🥇", "🥈", "🥉"][r.rank - 1] : r.rank}</span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 truncate font-bold">{r.username} <TierBadge tier={r.tier} /></div>
                <div className="text-xs text-muted">현금 {compact(r.cash_balance)} · 주식 {compact(r.positions_value)}</div>
              </div>
              <div className="text-right">
                <div className="font-bold tabular-nums">₩{compact(r.total_equity)}</div>
                <Change percent={r.return_pct} className="text-sm" />
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
