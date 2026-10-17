"use client";

import type { MarketHours } from "@/lib/api";
import { kstTime } from "@/lib/format";
import { useApi } from "@/lib/hooks";

export function useMarketHours() {
  return useApi<MarketHours>("/api/market/hours", 30_000);
}

export function MarketHoursBadge() {
  const { data } = useMarketHours();
  if (!data) return null;
  return data.open ? (
    <span className="whitespace-nowrap rounded-full border border-emerald-500/50 px-1.5 py-0.5 text-xs text-emerald-300"
      title={`장 마감 ${kstTime(data.session_close)}`}>
      ● 장중
    </span>
  ) : (
    <span className="whitespace-nowrap rounded-full border border-amber-500/50 px-1.5 py-0.5 text-xs text-amber-300"
      title={`다음 개장 ${kstTime(data.next_open)} · 지금은 예약 주문만 가능`}>
      ◐ 장마감 · {kstTime(data.next_open)} 개장
    </span>
  );
}
