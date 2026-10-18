"use client";

import Link from "next/link";
import { useState } from "react";
import { AssetRow } from "@/components/AssetRow";
import { Card, Change, Empty, ErrorBox } from "@/components/ui";
import type { Asset, ExpertPick } from "@/lib/api";
import { compact, credits } from "@/lib/format";
import { useApi } from "@/lib/hooks";

const TABS = [
  ["turnover", "거래대금"], ["market_cap", "시가총액"], ["volume", "거래량"], ["popularity", "인기도"],
  ["gainers", "상승률"], ["losers", "하락률"], ["experts", "초고수 PICK"],
] as const;
type Tab = (typeof TABS)[number][0];

function metricValue(tab: Tab, a: Asset) {
  switch (tab) {
    case "turnover": return `${compact(a.turnover_24h)} 크레딧`;
    case "market_cap": return `${compact(a.market_cap)} 크레딧`;
    case "volume": return `${credits(a.volume_24h)}주`;
    case "popularity": return a.popularity_score ? `${a.popularity_score} / 50` : "—";
    default: return <Change percent={a.change_pct} />;
  }
}

export default function Rankings() {
  const [tab, setTab] = useState<Tab>("turnover");
  const list = useApi<Asset[]>(tab === "experts" ? null : `/api/rankings?metric=${tab}&limit=50`, 15_000);
  const picks = useApi<ExpertPick[]>(tab === "experts" ? "/api/rankings/expert-picks?days=7" : null, 30_000);

  return (
    <div className="space-y-3">
      <div className="-mx-4 flex gap-1 overflow-x-auto px-4 pb-1">
        {TABS.map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`chip px-3 py-1.5 ${tab === k ? "chip-on" : ""}`}>{l}</button>
        ))}
      </div>
      {tab === "experts" ? (
        <Card title="🧠 초고수 PICK (최근 7일)" right={<span className="text-xs text-muted">자산 상위 고수들이 순매수한 종목 · 2명 이상 일치 시 표시</span>}>
          <ErrorBox error={picks.error} />
          {!picks.data ? <Empty>불러오는 중…</Empty> : picks.data.length === 0 ? <Empty>아직 집계할 고수 매수가 없습니다</Empty> : (
            <ol className="space-y-2">
              {picks.data.map((p) => (
                <li key={p.asset.id}>
                  <AssetRow a={p.asset} rank={p.rank} right={
                    <Link href={`/assets/${p.asset.id}`} className="shrink-0 text-right text-sm">
                      <div className="font-semibold">고수 {p.expert_buyers}명</div>
                      <div className="text-muted">순매수 {compact(p.net_buy_notional)}</div>
                    </Link>
                  } />
                </li>
              ))}
            </ol>
          )}
          <p className="mt-2 text-xs text-muted">고수 = 체결 5회 이상인 사용자 중 총자산 상위 30명. 개인은 공개되지 않습니다. 투자 권유가 아닙니다.</p>
        </Card>
      ) : (
        <Card title={`${TABS.find(([k]) => k === tab)?.[1]} 순위`} className="!bg-transparent [&>div]:px-0 [&>header]:px-1">
          <ErrorBox error={list.error} />
          {!list.data ? <Empty>불러오는 중…</Empty> : list.data.length === 0 ? <Empty>종목이 없습니다</Empty> : (
            <ol className="space-y-2">
              {list.data.map((a, i) => (
                <li key={a.id}>
                  <AssetRow a={a} rank={i + 1} right={
                    <Link href={`/assets/${a.id}`} className="shrink-0 text-right">
                      <div className="font-bold tabular-nums">₩{credits(a.current_price)}</div>
                      <div className="text-sm text-muted">{metricValue(tab, a)}</div>
                    </Link>
                  } />
                </li>
              ))}
            </ol>
          )}
        </Card>
      )}
    </div>
  );
}
