"use client";

import Link from "next/link";
import { useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { Card, Empty, LoginPrompt, Stat } from "@/components/ui";
import { api, ApiError, type DonationBox } from "@/lib/api";
import { credits, dateTime } from "@/lib/format";
import { useApi } from "@/lib/hooks";

export default function Donations() {
  const { user, ready } = useAuth();
  const { data, reload } = useApi<DonationBox>(user ? "/api/donations" : null);
  const [to, setTo] = useState("");
  const [amount, setAmount] = useState("10000");
  const [message, setMessage] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  if (!ready) return <Empty>…</Empty>;
  if (!user) return <LoginPrompt />;
  const box = data?.box;

  async function send(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setMsg(null);
    try {
      await api("/api/donations", { method: "POST", body: { recipient: to, amount: Number(amount), message: message || null } });
      setMsg(`✓ ${to}님에게 ${credits(Number(amount))} 크레딧을 기부했습니다.`);
      setMessage("");
      reload();
    } catch (err) {
      setMsg(err instanceof ApiError ? `⚠ ${err.message}` : "실패");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-4">
      {box && (
        <div className="grid grid-cols-2 gap-3">
          <Stat label="오늘 받은 기부" value={credits(box.received_today)}
            sub={<span className={box.full ? "text-amber-300" : "text-muted"}>{box.full ? "🎁 기부함이 가득 찼어요" : `남은 한도 ${credits(box.receive_remaining)}`}</span>} />
          <Stat label="오늘 보낸 기부" value={credits(box.sent_today)} sub={<span className="text-muted">남은 한도 {credits(box.send_remaining)}</span>} />
        </div>
      )}
      <Card title="🎁 채팅 기부함" right={<Link href="/chat" className="text-sm text-muted">채팅방 가기 ›</Link>}>
        <p className="text-sm text-muted">채팅방에서 <b className="text-text">＋ → 🎁 기부함 만들기</b>로 목표 금액(최대 500,000)을 정해 모금할 수 있어요. 기부함은 하루에 1번 만들 수 있고 24시간 동안 열려 있어요.</p>
        <p className="mt-2 text-sm">{data?.my_boxes?.can_open_today === false ? "오늘은 이미 기부함을 만들었어요. 내일 다시 만들 수 있어요." : "✓ 오늘 기부함을 만들 수 있어요."}</p>
        {data?.my_boxes && data.my_boxes.boxes.length > 0 && (
          <ul className="mt-3 space-y-2">
            {data.my_boxes.boxes.map((b) => (
              <li key={b.id} className="rounded-xl bg-panel2 px-3 py-2 text-sm">
                <div className="flex justify-between gap-2"><b className="truncate">{b.title}</b>
                  <span className="text-xs text-muted">{b.state === "open" ? "모금 중" : b.raised >= b.goal ? "목표 달성" : "종료"}</span></div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-bg"><div className="h-full bg-up" style={{ width: `${Math.min(100, (b.raised / b.goal) * 100)}%` }} /></div>
                <div className="mt-1 text-xs text-muted">₩{credits(b.raised)} / ₩{credits(b.goal)} · {b.room === "global" ? "전체 채팅" : "종목방"}</div>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card title="💝 직접 선물하기">
        <form onSubmit={send} className="space-y-3">
          <label className="block text-xs text-muted">받는 사람 아이디
            <input className="input mt-1" value={to} onChange={(e) => setTo(e.target.value)} autoComplete="off" />
          </label>
          <label className="block text-xs text-muted">금액 (100 ~ 500,000 크레딧)
            <input className="input mt-1" inputMode="numeric" value={amount} onChange={(e) => setAmount(e.target.value.replace(/\D/g, ""))} />
          </label>
          <div className="flex flex-wrap gap-2">
            {[10_000, 50_000, 100_000, 500_000].map((v) => (
              <button type="button" key={v} className="btn-ghost px-2 py-1 text-xs" onClick={() => setAmount(String(v))}>{credits(v)}</button>
            ))}
          </div>
          <label className="block text-xs text-muted">응원 메시지 (선택, 100자)
            <input className="input mt-1" maxLength={100} value={message} onChange={(e) => setMessage(e.target.value)} />
          </label>
          <button className="btn-primary w-full py-2" disabled={pending || !to || !amount}>{pending ? "처리 중…" : "기부하기"}</button>
          {msg && <p role="status" className="text-sm">{msg}</p>}
        </form>
        <ul className="mt-4 space-y-1 text-xs text-muted">
          <li>• 기부함 최대 금액: 한 사람이 하루에 받을 수 있는 기부는 500,000 크레딧까지입니다 (보내기도 동일).</li>
          <li>• 가입 72시간 후, 체결 5회 이상부터 기부할 수 있습니다. 같은 네트워크 계정끼리는 기부할 수 없습니다.</li>
          <li>• 크레딧은 현금 가치가 없으며 현금으로 바꿀 수 없습니다.</li>
        </ul>
      </Card>
      <Card title="기부 내역">
        {!data?.history.length ? <Empty>기부 내역이 없습니다</Empty> : (
          <ul className="divide-y divide-line">
            {data.history.map((h) => (
              <li key={h.id} className="flex items-center justify-between gap-2 py-2 text-sm">
                <div className="min-w-0">
                  <div>{h.outgoing ? `→ ${h.recipient}` : `← ${h.sender}`}</div>
                  {h.message && <div className="truncate text-xs text-muted">“{h.message}”</div>}
                </div>
                <div className="text-right">
                  <div className={`font-mono ${h.outgoing ? "text-down" : "text-up"}`}>{h.outgoing ? "−" : "+"}{credits(h.amount)}</div>
                  <div className="text-xs text-muted">{dateTime(h.created_at)}</div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
