"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, type Notification } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { useApi } from "@/lib/hooks";

const ICON: Record<string, string> = {
  order_filled: "✅", asset_halted: "⏸", new_video: "🎬", listing_approved: "🎉", listing_rejected: "✕",
  mission_reward: "🎁",
};

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const count = useApi<{ unread: number }>("/api/notifications/unread-count", 10_000);
  const list = useApi<Notification[]>(open ? "/api/notifications?limit=30" : null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const unread = count.data?.unread ?? 0;
  const markAll = async () => {
    await api("/api/notifications/read", { method: "POST", body: { all: true } });
    count.reload(); list.reload();
  };
  const markOne = async (id: number) => {
    await api("/api/notifications/read", { method: "POST", body: { ids: [id] } });
    count.reload();
    setOpen(false);
  };

  return (
    <div className="relative" ref={ref}>
      <button className="relative rounded-lg border border-line px-2.5 py-1.5 text-sm hover:bg-panel2"
        onClick={() => setOpen(!open)} aria-label={`알림 ${unread}개 읽지 않음`} aria-expanded={open}>
        🔔
        {unread > 0 && (
          <span className="absolute -right-1.5 -top-1.5 min-w-[18px] rounded-full bg-red-600 px-1 text-center text-[10px] font-bold leading-[18px] text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div role="dialog" aria-label="알림" className="absolute right-0 z-30 mt-2 w-[min(92vw,360px)] rounded-xl border border-line bg-panel shadow-2xl">
          <div className="flex items-center justify-between border-b border-line px-3 py-2">
            <span className="text-sm font-semibold">알림</span>
            <button className="text-xs text-muted hover:text-text" onClick={markAll} disabled={unread === 0}>모두 읽음</button>
          </div>
          <ul className="max-h-[60vh] overflow-y-auto">
            {!list.data ? <li className="p-4 text-center text-sm text-muted">불러오는 중…</li>
              : list.data.length === 0 ? <li className="p-4 text-center text-sm text-muted">알림이 없습니다</li>
              : list.data.map((n) => {
                const inner = (
                  <div className={`flex gap-2 px-3 py-2.5 text-sm hover:bg-panel2 ${n.read ? "opacity-60" : ""}`}>
                    <span aria-hidden>{ICON[n.kind] ?? "•"}</span>
                    <div className="min-w-0 flex-1">
                      <div className="flex justify-between gap-2">
                        <span className="truncate font-medium">{!n.read && <span className="sr-only">읽지 않음 </span>}{n.title}</span>
                        <span className="shrink-0 text-[11px] text-muted">{timeAgo(n.created_at)}</span>
                      </div>
                      {n.body && <p className="mt-0.5 text-xs text-muted">{n.body}</p>}
                    </div>
                    {!n.read && <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-sky-400" aria-hidden />}
                  </div>
                );
                return (
                  <li key={n.id} className="border-b border-line/50 last:border-0">
                    {n.link ? <Link href={n.link} onClick={() => markOne(n.id)}>{inner}</Link>
                      : <button className="w-full text-left" onClick={() => markOne(n.id)}>{inner}</button>}
                  </li>
                );
              })}
          </ul>
        </div>
      )}
    </div>
  );
}
