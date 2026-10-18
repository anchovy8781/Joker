"use client";

import { compact } from "@/lib/format";
import { useApi } from "@/lib/hooks";

type Cell = { qty: number; value: number };
type Flows = { groups: string[]; days: ({ date: string } & Record<string, Cell>)[]; total: Record<string, Cell> };

const signed = (n: number) => `${n > 0 ? "+" : n < 0 ? "−" : ""}${compact(Math.abs(n))}`;
const tone = (n: number) => (n > 0 ? "text-up" : n < 0 ? "text-down" : "text-muted");

/** 수급: who is net buying or selling this stock — today as diverging bars, then by day. */
export function InvestorFlow({ assetId }: { assetId: number }) {
  const { data } = useApi<Flows>(`/api/assets/${assetId}/flows?days=5`, 15_000);
  if (!data) return <div className="py-6 text-center text-sm text-muted">불러오는 중…</div>;
  if (!data.days.length) return <div className="py-6 text-center text-sm text-muted">아직 체결이 없어요</div>;
  const today = data.days[0];
  const max = Math.max(1, ...data.groups.map((g) => Math.abs(today[g].value)));
  return (
    <div className="space-y-4">
      <div>
        <div className="mb-2 flex items-baseline justify-between text-sm">
          <span className="font-semibold">오늘 순매수 <span className="text-xs font-normal text-muted">({today.date.slice(5).replace("-", "/")})</span></span>
          <span className="text-xs text-muted">금액 기준 · 크레딧</span>
        </div>
        <ul className="space-y-2">
          {data.groups.map((g) => {
            const v = today[g].value;
            const w = (Math.abs(v) / max) * 50;
            return (
              <li key={g} className="grid grid-cols-[3.5rem_1fr_4.5rem] items-center gap-2 text-sm">
                <span className="text-muted">{g}</span>
                <span className="relative h-5 rounded-md bg-panel2" aria-hidden>
                  <span className="absolute inset-y-0 left-1/2 w-px bg-line" />
                  <span className={`absolute inset-y-0.5 rounded ${v >= 0 ? "bg-up" : "bg-down"}`}
                    style={v >= 0 ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }} />
                </span>
                <span className={`text-right font-semibold tabular-nums ${tone(v)}`}>{signed(v)}</span>
              </li>
            );
          })}
        </ul>
      </div>
      <div className="-mx-1 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-muted">
              <th className="px-1 py-1 text-left font-medium">날짜 (주)</th>
              {data.groups.map((g) => <th key={g} className="px-1 py-1 text-right font-medium">{g}</th>)}
            </tr>
          </thead>
          <tbody>
            {data.days.map((d) => (
              <tr key={d.date} className="border-t border-line/60">
                <td className="px-1 py-1.5 text-muted">{d.date.slice(5).replace("-", "/")}</td>
                {data.groups.map((g) => <td key={g} className={`px-1 py-1.5 text-right tabular-nums ${tone(d[g].qty)}`}>{signed(d[g].qty)}</td>)}
              </tr>
            ))}
            <tr className="border-t border-line font-semibold">
              <td className="px-1 py-1.5">합계</td>
              {data.groups.map((g) => <td key={g} className={`px-1 py-1.5 text-right tabular-nums ${tone(data.total[g].qty)}`}>{signed(data.total[g].qty)}</td>)}
            </tr>
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted">+는 순매수(빨강), −는 순매도(파랑). 개인 = 플레이어와 개인 투자자 봇, 기관·외국인 = 기관·외국인 봇, 세력 = 큰손 봇. 시장조성자는 빠져요.</p>
    </div>
  );
}
