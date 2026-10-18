"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { ChannelAvatar } from "./ChannelAvatar";
import { ChannelPreview, type ChannelPreviewData } from "./ChannelPreview";

type Hit = { youtube_channel_id: string; title: string; handle: string | null; thumbnail_url: string | null; subscriber_text: string | null };

/** Type a channel name (or @handle / URL / ID) → pick from YouTube results → the channel ID
 *  is filled in automatically and the full profile is previewed. */
export function ChannelPicker({ onPicked, placeholder = "채널 이름 또는 @핸들 검색" }: {
  onPicked: (p: ChannelPreviewData | null) => void; placeholder?: string;
}) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [picked, setPicked] = useState<ChannelPreviewData | null>(null);
  const [loadingPick, setLoadingPick] = useState(false);

  useEffect(() => {
    const v = q.trim();
    setErr(null);
    if (picked || v.length < 2) { setHits(null); return; }
    let alive = true;
    setSearching(false);
    const t = setTimeout(async () => {
      setSearching(true);
      try {
        const r = await api<Hit[]>(`/api/channels/search?q=${encodeURIComponent(v)}`, { timeoutMs: 25_000 });
        if (alive) setHits(r);
      } catch (e) {
        if (alive) { setHits([]); setErr(e instanceof ApiError ? e.message : "검색하지 못했어요"); }
      } finally {
        if (alive) setSearching(false);
      }
    }, 500);
    return () => { alive = false; clearTimeout(t); };
  }, [q, picked]);

  async function pick(h: Hit) {
    setLoadingPick(true);
    setErr(null);
    try {
      const p = await api<ChannelPreviewData>(`/api/listing-requests/preview?channel=${encodeURIComponent(h.youtube_channel_id)}`, { timeoutMs: 30_000 });
      const full = { ...p, handle: p.handle ?? h.handle, thumbnail_url: p.thumbnail_url ?? h.thumbnail_url,
                     subscriber_text: p.subscriber_text ?? h.subscriber_text };
      setPicked(full);
      onPicked(full);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "채널 정보를 불러오지 못했어요");
    } finally {
      setLoadingPick(false);
    }
  }

  function clear() {
    setPicked(null);
    setHits(null);
    onPicked(null);
  }

  if (picked) {
    return (
      <div className="space-y-2">
        <ChannelPreview p={picked} />
        <div className="flex items-center gap-2 rounded-xl bg-panel2 px-3 py-2 text-sm">
          <span className="shrink-0 text-muted">채널 ID</span>
          <code className="min-w-0 flex-1 truncate font-mono text-text" aria-label="자동 입력된 채널 ID">{picked.youtube_channel_id}</code>
          <span className="shrink-0 rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs font-bold text-emerald-300">자동 입력</span>
        </div>
        <button type="button" className="text-sm text-muted underline" onClick={clear}>다른 채널 고르기</button>
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <input className="input font-sans text-base" value={q} onChange={(e) => setQ(e.target.value)} placeholder={placeholder}
        aria-label="채널 검색" autoCapitalize="off" autoCorrect="off" />
      {(searching || loadingPick) && <p className="text-sm text-muted">🔎 {loadingPick ? "채널 정보를 불러오는 중…" : "찾는 중…"}</p>}
      {err && <p role="alert" className="text-sm text-amber-300">⚠ {err}</p>}
      {hits && hits.length === 0 && !searching && !err && <p className="text-sm text-muted">검색 결과가 없어요. 다른 이름이나 @핸들로 찾아보세요.</p>}
      {hits && hits.length === 0 && !searching && !loadingPick && /^@?[\w.\-]{3,30}$/u.test(q.trim()) && (
        <button type="button" className="btn-ghost w-full py-3"
          onClick={() => pick({ youtube_channel_id: q.trim().startsWith("@") ? q.trim() : `@${q.trim()}`, title: q.trim(), handle: null, thumbnail_url: null, subscriber_text: null })}>
          {q.trim().startsWith("@") ? q.trim() : `@${q.trim()}`} 핸들로 바로 확인하기
        </button>
      )}
      {hits && hits.length > 0 && (
        <ul className="overflow-hidden rounded-2xl bg-panel2" role="listbox" aria-label="검색 결과">
          {hits.map((h) => (
            <li key={h.youtube_channel_id}>
              <button type="button" role="option" aria-selected={false} onClick={() => pick(h)}
                className="flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-panel">
                <ChannelAvatar src={h.thumbnail_url} title={h.title} seed={h.youtube_channel_id} size={40} className="rounded-full" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-semibold">{h.title}</span>
                  <span className="block truncate text-xs text-muted">{[h.handle, h.subscriber_text].filter(Boolean).join(" · ") || h.youtube_channel_id}</span>
                </span>
                <span className="text-sm text-muted">선택</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
