"use client";

import { compact, pct } from "@/lib/format";
import { useApi } from "@/lib/hooks";

type Window = { change: number; pct: number | null; span_hours: number; full: boolean } | null;
type Series = { current: number; as_of: string; d1: Window; d7: Window; d28: Window } | null;
type Growth = { hidden_subscriber_count: boolean; subscribers: Series; views: Series };

function spanLabel(h: number): string {
  if (h < 1) return `${Math.max(1, Math.round(h * 60))}분`;
  if (h < 48) return `${Math.round(h)}시간`;
  return `${Math.round(h / 24)}일`;
}

function Cell({ w, days }: { w: Window; days: number }) {
  if (!w) return <div className="text-muted">수집 중</div>;
  const color = w.change > 0 ? "text-up" : w.change < 0 ? "text-down" : "text-muted";
  return (
    <div>
      <div className={`font-bold tabular-nums ${color}`}>{w.change > 0 ? "+" : ""}{compact(w.change)}</div>
      {w.pct !== null && <div className="text-xs text-muted tabular-nums">{pct(w.pct)}</div>}
      {!w.full && <div className="text-[11px] text-muted">최근 {spanLabel(w.span_hours)} 기준</div>}
      <span className="sr-only">{days}일 변화</span>
    </div>
  );
}

/** Subscribers and views now, plus their change over 1 / 7 / 28 days. A channel with less
 *  history than a window shows the change over what there is, labelled with its length. */
export function ChannelGrowth({ assetId, fallback }: { assetId: number; fallback: { subscribers: number | null; views: number | null } }) {
  const { data } = useApi<Growth>(`/api/assets/${assetId}/growth`, 60_000);
  const hidden = data?.hidden_subscriber_count;
  const rows: [string, Series, number | null, boolean][] = [
    ["구독자", data?.subscribers ?? null, fallback.subscribers, !!hidden],
    ["조회 수", data?.views ?? null, fallback.views, false],
  ];
  return (
    <div className="space-y-3">
      {rows.map(([label, s, fb, isHidden]) => (
        <div key={label} className="rounded-2xl bg-panel2 p-3">
          <div className="flex items-baseline justify-between">
            <span className="text-sm text-muted">{label}</span>
            <span className="text-lg font-bold tabular-nums">{isHidden ? "비공개" : compact(s?.current ?? fb)}</span>
          </div>
          {!isHidden && (
            <div className="mt-2 grid grid-cols-3 gap-2 text-sm">
              {([["1일", s?.d1, 1], ["7일", s?.d7, 7], ["28일", s?.d28, 28]] as const).map(([l, w, d]) => (
                <div key={l}>
                  <div className="text-xs text-muted">{l} 변화</div>
                  <Cell w={w ?? null} days={d} />
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
