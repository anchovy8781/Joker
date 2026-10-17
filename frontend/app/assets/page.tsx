"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { AssetRow, ChangeText, channelName } from "@/components/AssetRow";
import { Empty, ErrorBox } from "@/components/ui";
import type { Asset } from "@/lib/api";
import { useFavorites } from "@/lib/favorites";
import { useApi } from "@/lib/hooks";

const SORTS = [
  { key: "fav", label: "☆ 즐겨찾기" },
  { key: "turnover", label: "거래대금순" },
  { key: "gain", label: "상승률순" },
  { key: "fall", label: "하락률순" },
  { key: "high", label: "가격 높은순" },
  { key: "low", label: "가격 낮은순" },
  { key: "cap", label: "시가총액순" },
  { key: "pop", label: "인기도순" },
  { key: "new", label: "신규 상장순" },
] as const;
type SortKey = (typeof SORTS)[number]["key"];

function sortAssets(list: Asset[], key: SortKey, favs: number[]): Asset[] {
  const out = key === "fav" ? list.filter((a) => favs.includes(a.id)) : [...list];
  const by = (f: (a: Asset) => number, desc = true) => out.sort((x, y) => (desc ? f(y) - f(x) : f(x) - f(y)));
  switch (key) {
    case "fav": case "turnover": return by((a) => a.turnover_24h);
    case "gain": return by((a) => a.change_pct);
    case "fall": return by((a) => a.change_pct, false);
    case "high": return by((a) => a.current_price);
    case "low": return by((a) => a.current_price, false);
    case "cap": return by((a) => a.market_cap);
    case "pop": return by((a) => a.popularity_score ?? 0);
    case "new": return by((a) => (a.channel.listed_at ? Date.parse(a.channel.listed_at) : 0));
  }
}

export default function AssetsPage() {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [sort, setSort] = useState<SortKey>("turnover");
  const favs = useFavorites();

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 250);
    return () => clearTimeout(t);
  }, [q]);

  const all = useApi<Asset[]>("/api/assets", 10_000);
  const list = useMemo(() => {
    const term = debounced.trim().toLowerCase();
    const found = (all.data ?? []).filter((a) => !term || a.symbol.toLowerCase().includes(term)
      || a.channel.title.toLowerCase().includes(term) || (a.channel.handle ?? "").toLowerCase().includes(term));
    return sortAssets(found, sort, favs.ids);
  }, [all.data, debounced, sort, favs.ids]);
  const top = useMemo(() => [...(all.data ?? [])].sort((x, y) => y.change_pct - x.change_pct)[0], [all.data]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">주식</h1>
        <Link href="/listings" className="rounded-full bg-text px-4 py-2 text-sm font-bold text-bg">⊕ 상장하러 가기</Link>
      </div>

      <div className="grid grid-cols-2 rounded-2xl bg-panel p-5">
        <div className="border-r border-line pr-4">
          <div className="text-sm text-muted">상장된 종목</div>
          <div className="mt-1 text-2xl font-bold">{all.data ? `${all.data.length}개` : "—"}</div>
        </div>
        <div className="min-w-0 pl-4">
          <div className="text-sm text-muted">오늘의 급상승</div>
          {top && top.change_pct > 0 ? (
            <Link href={`/assets/${top.id}`}>
              <div className="mt-1 truncate text-lg font-bold">{channelName(top)}</div>
              <ChangeText percent={top.change_pct} />
            </Link>
          ) : <div className="mt-1 text-lg font-bold text-muted">—</div>}
        </div>
      </div>

      <input className="input rounded-2xl border-0 bg-panel py-3.5 font-sans text-base" placeholder="🔍  종목명, 채널명, @핸들 검색"
        value={q} onChange={(e) => setQ(e.target.value)} aria-label="검색" />

      <div className="no-scrollbar -mx-4 flex gap-2 overflow-x-auto px-4" role="tablist" aria-label="정렬">
        {SORTS.map((s) => (
          <button key={s.key} role="tab" aria-selected={sort === s.key} onClick={() => setSort(s.key)}
            className={`chip ${sort === s.key ? "chip-on" : ""}`}>{s.label}</button>
        ))}
      </div>

      <details className="rounded-2xl bg-panel px-5 py-4">
        <summary className="cursor-pointer text-base font-bold">시세는 이렇게 움직여요</summary>
        <ul className="mt-3 space-y-2 text-sm text-muted">
          <li>📈 실제 유튜브 지표(구독자·조회수 증가 속도, 좋아요 반응)가 매일의 기준 가격을 움직여요.</li>
          <li>🔁 게임 안 매수·매도가 체결되는 순간 가격이 바뀌어요. 거래가 몰릴수록 크게 움직여요.</li>
          <li>🔥 신규 영상·유명인 신규 채널은 할증 수수료가 붙어요 (손실 매도 시 매도 수수료 면제).</li>
          <li>상승은 <span className="text-up">빨강</span>, 하락은 <span className="text-down">파랑</span>으로 표시돼요.</li>
        </ul>
      </details>

      <ErrorBox error={all.error} />
      {!all.data ? <Empty>불러오는 중…</Empty> : list.length === 0 ? (
        <div className="rounded-2xl bg-panel">
          <Empty>{sort === "fav" ? "☆를 눌러 즐겨찾기를 추가해 보세요" : "조건에 맞는 종목이 없어요"}</Empty>
        </div>
      ) : (
        <div className="space-y-2.5">
          {list.map((a) => <AssetRow key={a.id} a={a} fav={favs.has(a.id)} onFav={favs.toggle} />)}
        </div>
      )}
    </div>
  );
}
