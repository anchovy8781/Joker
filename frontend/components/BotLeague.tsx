"use client";

import { Change, Empty, ErrorBox } from "@/components/ui";
import { compact, kstTime } from "@/lib/format";
import { useApi } from "@/lib/hooks";

type Row = {
  name: string; group: string; kind: string; equity: number; profit: number; return: number;
  last_return: number | null; rank: number | null; conviction: number; rewards: number; wins: number; podiums: number;
  strategy?: { best: string; label: string; mix: Record<string, number>; hit_rate: number | null };
};
type League = { bots: Row[]; last_eval: string | null; history: { at: string; top: { name: string; ret: number; reward: number }[] }[]; interval_minutes: number };

const MEDAL = ["🥇", "🥈", "🥉"];
const GROUP_TONE: Record<string, string> = { 개인: "bg-gray-500/15", 기관: "bg-blue-500/15", 외국인: "bg-emerald-500/15", 세력: "bg-red-500/15" };

/** 봇 리그: virtual traders ranked by return; winners get rewards and more order flow. */
export function BotLeague() {
  const { data, error } = useApi<League>("/api/bots/league", 30_000);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Empty>불러오는 중…</Empty>;
  if (!data.bots.length) return <Empty>아직 봇이 없습니다</Empty>;
  const last = data.history[0];
  return (
    <div className="space-y-3">
      {last && (
        <div className="rounded-2xl bg-panel p-4">
          <div className="mb-2 flex items-baseline justify-between">
            <span className="font-semibold">지난 라운드 시상</span>
            <span className="text-xs text-muted">{kstTime(last.at)} 정산</span>
          </div>
          <ol className="space-y-1.5">
            {last.top.map((t, i) => (
              <li key={t.name} className="flex items-center justify-between gap-2 text-sm">
                <span className="min-w-0 truncate">{MEDAL[i]} {t.name}</span>
                <span className="shrink-0 tabular-nums">
                  <Change percent={t.ret * 100} />
                  {t.reward > 0 && <span className="ml-2 font-semibold">+{compact(t.reward)}</span>}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
      <ol className="space-y-2">
        {data.bots.map((b, i) => (
          <li key={b.name} className="rounded-2xl bg-panel p-3">
            <div className="flex items-center gap-3">
              <span className="w-6 shrink-0 text-center font-bold text-muted tabular-nums">{i + 1}</span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[11px] ${GROUP_TONE[b.group] ?? ""}`}>{b.group}</span>
                  <span className="truncate font-semibold">{b.name}</span>
                </div>
                <div className="mt-0.5 truncate text-xs text-muted">
                  자산 {compact(b.equity)} · 확신도 ×{b.conviction.toFixed(2)}
                  {b.podiums > 0 && <> · 입상 {b.podiums}회{b.wins > 0 && ` (1위 ${b.wins})`}</>}
                </div>
              </div>
              <div className="shrink-0 text-right">
                <Change percent={b.return * 100} className="text-sm font-semibold" />
                <div className="text-xs text-muted">
                  {b.last_return === null ? "라운드 대기" : <>최근 <Change percent={b.last_return * 100} /></>}
                </div>
              </div>
            </div>
            {b.strategy && (
              <div className="mt-2 pl-9">
                <div className="mb-1 flex justify-between text-xs">
                  <span>주력 전략 <b>{b.strategy.label}</b></span>
                  {b.strategy.hit_rate !== null && <span className="text-muted">적중률 {(b.strategy.hit_rate * 100).toFixed(0)}%</span>}
                </div>
                <div className="flex h-1.5 overflow-hidden rounded-full bg-black/10">
                  {Object.entries(b.strategy.mix).map(([k, v], j) => (
                    <div key={k} title={`${k} ${(v * 100).toFixed(0)}%`} style={{ width: `${v * 100}%` }}
                      className={["bg-blue-500", "bg-amber-500", "bg-red-500", "bg-emerald-500"][j]} />
                  ))}
                </div>
                <div className="mt-1 flex flex-wrap gap-x-2 text-[11px] text-muted">
                  {Object.entries(b.strategy.mix).map(([k, v], j) => (
                    <span key={k}><span className={["text-blue-500", "text-amber-500", "text-red-500", "text-emerald-500"][j]}>●</span> {k} {(v * 100).toFixed(0)}%</span>
                  ))}
                </div>
              </div>
            )}
          </li>
        ))}
      </ol>
      <p className="px-1 text-xs text-muted">
        {data.interval_minutes}분마다 수익률을 정산해 1~3위 봇에게 보상(시작 자본의 3·2·1%)을 줍니다.
        수익을 낸 봇은 확신도가 올라 주문 규모와 비중이 커지고, 손실 봇은 줄어듭니다.
        기관·외국인은 어떤 전략이 돈을 벌었는지 학습해 다음 판단에 더 자주 씁니다. 수익률 = (자산 − 지급받은 자본) ÷ 지급받은 자본.
      </p>
    </div>
  );
}
