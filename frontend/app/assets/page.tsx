"use client";

import { useEffect, useState } from "react";
import { AssetTable } from "@/components/AssetTable";
import { Card, Empty, ErrorBox } from "@/components/ui";
import type { Asset } from "@/lib/api";
import { useApi } from "@/lib/hooks";

export default function AssetsPage() {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [status, setStatus] = useState("");
  const [source, setSource] = useState("");
  const [sort, setSort] = useState("symbol");

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 250);
    return () => clearTimeout(t);
  }, [q]);

  const params = new URLSearchParams({ sort });
  if (debounced) params.set("q", debounced);
  if (status) params.set("status", status);
  if (source) params.set("source", source);
  const { data, error } = useApi<Asset[]>(`/api/assets?${params}`, 15_000);

  return (
    <Card title="종목 목록">
      <div className="mb-4 grid gap-2 md:grid-cols-4">
        <input className="input" placeholder="심볼 또는 채널명 검색" value={q} onChange={(e) => setQ(e.target.value)} aria-label="검색" />
        <select className="input" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="거래 상태">
          <option value="">전체 상태</option>
          <option value="active">거래 가능</option>
          <option value="halted">거래 정지</option>
        </select>
        <select className="input" value={source} onChange={(e) => setSource(e.target.value)} aria-label="데이터 출처">
          <option value="">전체 데이터 출처</option>
          <option value="youtube_api">실제 데이터 (YouTube API)</option>
          <option value="demo">데모 데이터</option>
        </select>
        <select className="input" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="정렬">
          <option value="symbol">심볼순</option>
          <option value="change">등락률순</option>
          <option value="volume">거래량순</option>
          <option value="price">가격순</option>
        </select>
      </div>
      <ErrorBox error={error} />
      {data ? <AssetTable assets={data} /> : <Empty>불러오는 중…</Empty>}
    </Card>
  );
}
