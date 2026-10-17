"use client";

import Link from "next/link";
import { useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { Card, Empty, LoginPrompt } from "@/components/ui";
import { api, ApiError, type Account, type ListingRequest, type ListingWindow } from "@/lib/api";
import { credits, dateTime, kstTime } from "@/lib/format";
import { useApi } from "@/lib/hooks";

const STATUS: Record<string, string> = { pending: "⏳ 심사 중", listed: "✅ 상장됨", rejected: "✕ 거절", delisted: "상장 폐지" };

export default function Listings() {
  const { user, ready } = useAuth();
  const win = useApi<ListingWindow>("/api/listing-requests/status", 30_000);
  const mine = useApi<ListingRequest[]>(user ? "/api/listing-requests" : null);
  const account = useApi<Account>(user ? "/api/account" : null);
  const [channel, setChannel] = useState("");
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  if (!ready) return <Empty>…</Empty>;
  if (!user) return <LoginPrompt />;
  const w = win.data;
  const fee = w?.listing_fee ?? 0;
  const short = account.data ? account.data.available_cash < fee : false;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setMsg(null);
    try {
      await api("/api/listing-requests", { method: "POST", body: { channel, note: note || null } });
      setMsg("✓ 상장 요청이 접수되었습니다. 운영자 승인 후 상장됩니다.");
      setChannel(""); setNote("");
      mine.reload(); win.reload(); account.reload();
    } catch (err) {
      setMsg(err instanceof ApiError ? `⚠ ${err.message}` : "실패");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-4">
      <Card title="📝 상장 요청">
        {w && (
          w.limit_reached ? (
            <div role="alert" className="mb-3 rounded-lg border border-amber-500/50 bg-amber-500/10 p-3 text-sm text-amber-200">
              ⚠ 오늘의 상장 요청 최대한도({w.limit}개)에 도달했습니다. 내일 오후 7시에 다시 열립니다.
            </div>
          ) : !w.open ? (
            <div className="mb-3 rounded-lg border border-line bg-panel2 p-3 text-sm">
              🕖 상장 요청은 매일 <b>오후 7시</b>부터 가능합니다. 다음 접수: {kstTime(w.next_opens_at)}
            </div>
          ) : (
            <div className="mb-3 rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm">
              ● 접수 중 · 오늘 남은 요청 <b>{w.remaining}</b> / {w.limit}
            </div>
          )
        )}
        <form onSubmit={submit} className="space-y-3">
          <label className="block text-xs text-muted">유튜브 채널 핸들만 입력하면 돼요 (채널 주소도 가능)
            <input className="input mt-1 font-sans text-base" value={channel} onChange={(e) => setChannel(e.target.value)}
              placeholder="@채널핸들" autoCapitalize="off" autoCorrect="off" />
          </label>
          <div className="flex items-center justify-between rounded-xl bg-panel2 px-3 py-2.5 text-sm">
            <span className="text-muted">상장 신청 비용</span>
            <span className="font-bold tabular-nums">₩{credits(fee)}</span>
          </div>
          {account.data && (
            <p className={`text-xs ${short ? "text-amber-300" : "text-muted"}`}>
              주문 가능 금액 ₩{credits(account.data.available_cash)}
              {short && <> · 비용이 부족해요. <Link href="/missions" className="underline">미션</Link>으로 모아 보세요.</>}
            </p>
          )}
          <label className="block text-xs text-muted">요청 사유 (선택)
            <input className="input mt-1" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          <button className="btn-primary w-full py-2.5" disabled={pending || !channel || !w?.open || w?.limit_reached || short}>
            {w?.limit_reached ? "요청 최대한도 도달" : !w?.open ? "오후 7시부터 요청 가능" : short ? "비용 부족" : pending ? "처리 중…" : `₩${credits(fee)} 내고 상장 요청하기`}
          </button>
          {msg && <p role="status" className="text-sm">{msg}</p>}
        </form>
        <p className="mt-3 text-[11px] text-muted">모든 상장은 운영자 승인제입니다. 거절되면 신청 비용은 전액 환불돼요. 전체 사용자 합계 하루 25개까지 접수되며, 내가 요청한 채널이 상장되면 미션 보상을 받을 수 있어요.</p>
      </Card>
      <Card title="내 요청">
        {!mine.data?.length ? <Empty>요청 내역이 없습니다</Empty> : (
          <ul className="divide-y divide-line">
            {mine.data.map((r) => (
              <li key={r.id} className="flex items-center justify-between gap-2 py-2 text-sm">
                <div className="min-w-0"><div className="truncate">{r.title}</div><div className="font-mono text-[10px] text-muted">{r.youtube_channel_id}</div></div>
                <div className="text-right"><div>{STATUS[r.listing_status] ?? r.listing_status}{r.listing_status === "rejected" && r.fee_paid > 0 && <span className="text-xs text-muted"> · 환불됨</span>}</div><div className="text-[11px] text-muted">{dateTime(r.created_at)}</div></div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
