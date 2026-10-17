"use client";

import { useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { Card, Empty, LoginPrompt } from "@/components/ui";
import { api, ApiError, type Mission } from "@/lib/api";
import { credits } from "@/lib/format";
import { useApi } from "@/lib/hooks";

export default function Missions() {
  const { user, ready } = useAuth();
  const { data, reload } = useApi<Mission[]>(user ? "/api/missions" : null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  if (!ready) return <Empty>…</Empty>;
  if (!user) return <LoginPrompt />;

  async function claim(code: string) {
    setBusy(code);
    setMsg(null);
    try {
      const r = await api<{ reward: number }>(`/api/missions/${code}/claim`, { method: "POST" });
      setMsg(`🎁 ${credits(r.reward)} 크레딧을 받았습니다!`);
      reload();
    } catch (e) {
      setMsg(e instanceof ApiError ? `⚠ ${e.message}` : "실패");
    } finally {
      setBusy(null);
    }
  }

  const groups: [string, Mission[]][] = [
    ["오늘의 미션 (매일 자정 초기화)", (data ?? []).filter((m) => m.period === "daily")],
    ["도전 미션 (1회)", (data ?? []).filter((m) => m.period === "once")],
  ];
  return (
    <div className="space-y-4">
      {msg && <div role="status" className="rounded-lg border border-line bg-panel2 px-3 py-2 text-sm">{msg}</div>}
      {groups.map(([title, list]) => (
        <Card key={title} title={title}>
          {!data ? <Empty>불러오는 중…</Empty> : (
            <ul className="space-y-3">
              {list.map((m) => (
                <li key={m.code} className="flex items-center gap-3 rounded-xl bg-panel2 p-3">
                  <div className="min-w-0 flex-1">
                    <div className="font-semibold">{m.claimed ? "✅ " : ""}{m.title}</div>
                    <div className="text-xs text-muted">{m.description}</div>
                    <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-line" role="progressbar"
                      aria-valuemin={0} aria-valuemax={m.target} aria-valuenow={m.progress}>
                      <div className="h-full bg-emerald-400" style={{ width: `${(m.progress / m.target) * 100}%` }} />
                    </div>
                    <div className="mt-1 text-[11px] text-muted">{m.progress}/{m.target} · 보상 {credits(m.reward)} 크레딧</div>
                  </div>
                  <button className="btn-primary shrink-0" disabled={!m.claimable || busy === m.code} onClick={() => claim(m.code)}>
                    {m.claimed ? "완료" : m.claimable ? "보상 받기" : "진행 중"}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      ))}
      <p className="text-xs text-muted">보상은 서버가 조건을 확인한 뒤 지급하며, 같은 미션은 기간당 한 번만 받을 수 있습니다. 크레딧은 현금 가치가 없습니다.</p>
    </div>
  );
}
