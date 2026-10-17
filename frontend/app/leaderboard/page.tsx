"use client";

import { useAuth } from "@/components/AuthProvider";
import { Card, Change, Empty, ErrorBox } from "@/components/ui";
import type { LeaderRow } from "@/lib/api";
import { credits } from "@/lib/format";
import { useApi } from "@/lib/hooks";

export default function Leaderboard() {
  const { user } = useAuth();
  const { data, error } = useApi<LeaderRow[]>("/api/leaderboard?limit=100", 30_000);
  return (
    <Card title="전체 자산 랭킹" right={<span className="text-xs text-muted">서버 계좌 + 현재 시장가 평가 · 시작 자금 1,000,000 대비</span>}>
      <ErrorBox error={error} />
      {!data ? <Empty>불러오는 중…</Empty> : data.length === 0 ? <Empty>아직 참가자가 없습니다</Empty> : (
        <div className="-mx-4 overflow-x-auto px-4">
          <table className="table">
            <thead><tr><th>순위</th><th>사용자</th><th className="num">총자산</th><th className="num">현금</th><th className="num">주식 평가액</th><th className="num">수익률</th></tr></thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.username} className={r.username === user?.username ? "bg-panel2" : ""}>
                  <td className="font-mono">{r.rank <= 3 ? ["🥇", "🥈", "🥉"][r.rank - 1] : r.rank}</td>
                  <td>{r.username}{r.username === user?.username && <span className="ml-1 text-xs text-muted">(나)</span>}</td>
                  <td className="num">{credits(r.total_equity)}</td>
                  <td className="num">{credits(r.cash_balance)}</td>
                  <td className="num">{credits(r.positions_value)}</td>
                  <td className="num"><Change percent={r.return_pct} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
