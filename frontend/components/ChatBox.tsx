"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type ChatMessage, type DonationBoxInfo } from "@/lib/api";
import { credits } from "@/lib/format";
import { useAuth } from "./AuthProvider";

const QUICK = [1_000, 5_000, 10_000, 50_000];

function Progress({ box }: { box: DonationBoxInfo }) {
  const pctDone = Math.min(100, Math.round((box.raised / box.goal) * 100));
  return (
    <div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-bg"><div className="h-full rounded-full bg-up transition-all" style={{ width: `${pctDone}%` }} /></div>
      <div className="mt-1 flex justify-between text-xs text-muted">
        <span><b className="text-text">₩{credits(box.raised)}</b> / ₩{credits(box.goal)}</span>
        <span>{pctDone}%</span>
      </div>
    </div>
  );
}

/** Donation box card shown inline in the chat (and pinned while it is open). */
function BoxCard({ box, me, onGive }: { box: DonationBoxInfo; me?: string; onGive: (box: DonationBoxInfo) => void }) {
  const label = box.state === "open" ? null : box.raised >= box.goal ? "🎉 목표 달성" : box.state === "expired" ? "기간 종료" : "마감";
  return (
    <div className="w-full max-w-sm rounded-2xl border border-up/30 bg-up/5 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-bold text-up">🎁 기부함</span>
        <span className="text-xs text-muted">{box.owner}</span>
      </div>
      <div className="mt-1 text-[15px] font-bold">{box.title}</div>
      <Progress box={box} />
      {label ? <div className="mt-2 text-center text-sm font-semibold text-muted">{label}</div>
        : box.owner !== me && <button onClick={() => onGive(box)} className="btn-primary mt-2 w-full py-2">기부하기</button>}
    </div>
  );
}

type Sheet = { kind: "give"; box: DonationBoxInfo } | { kind: "tip"; to: string } | { kind: "open" } | null;

/** Polling chat for one room ("global" or "asset:<id>") with in-chat donation boxes and gifts. */
export function ChatBox({ room, height = "h-[55vh]" }: { room: string; height?: string }) {
  const { user } = useAuth();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [openBoxes, setOpenBoxes] = useState<DonationBoxInfo[]>([]);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [sheet, setSheet] = useState<Sheet>(null);
  const [amount, setAmount] = useState("5000");
  const [note, setNote] = useState("");
  const [title, setTitle] = useState("");
  const [tipTo, setTipTo] = useState("");
  const lastId = useRef(0);
  const listRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  const load = useCallback(async () => {
    try {
      const [fresh, boxes] = await Promise.all([
        api<ChatMessage[]>(`/api/chat/${room}/messages${lastId.current ? `?after_id=${lastId.current}` : ""}`),
        api<DonationBoxInfo[]>(`/api/chat/${room}/boxes`),
      ]);
      setOpenBoxes(boxes);
      if (fresh.length) {
        lastId.current = fresh[fresh.length - 1].id;
        setMessages((m) => [...m, ...fresh].slice(-300));
      }
    } catch { /* keep last state; next poll retries */ }
  }, [room]);

  useEffect(() => {
    lastId.current = 0;
    setMessages([]);
    load();
    const t = setInterval(() => { if (!document.hidden) load(); }, 3000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (stick.current && listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [messages]);

  // Box cards in old messages carry the state from when they were loaded; the open-box
  // list is refreshed on every poll, so anything no longer in it has finished.
  const live = new Map(openBoxes.map((b) => [b.id, b]));
  const current = (b: DonationBoxInfo): DonationBoxInfo =>
    live.get(b.id) ?? (b.state === "open" ? { ...b, state: "closed" } : b);

  async function call(fn: () => Promise<unknown>, ok: string) {
    setSending(true);
    setError(null);
    try {
      await fn();
      setNotice(ok);
      setSheet(null);
      setNote("");
      stick.current = true;
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "실패했어요");
    } finally {
      setSending(false);
    }
  }

  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim() || sending) return;
    await call(() => api(`/api/chat/${room}/messages`, { method: "POST", body: { body: text } }).then(() => setText("")), "");
    setNotice(null);
  }

  const amt = Number(amount) || 0;
  return (
    <div className="flex flex-col">
      {openBoxes.length > 0 && (
        <div className="no-scrollbar -mx-1 mb-3 flex gap-2 overflow-x-auto px-1" aria-label="진행 중인 기부함">
          {openBoxes.map((b) => (
            <button key={b.id} onClick={() => b.owner !== user?.username && setSheet({ kind: "give", box: b })}
              className="w-56 shrink-0 rounded-2xl bg-up/10 px-3 py-2 text-left">
              <div className="truncate text-sm font-bold">🎁 {b.title}</div>
              <div className="truncate text-xs text-muted">{b.owner} · ₩{credits(b.raised)} / ₩{credits(b.goal)}</div>
            </button>
          ))}
        </div>
      )}
      <div ref={listRef} className={`${height} space-y-3 overflow-y-auto pr-1`}
        onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40; }}
        role="log" aria-live="polite" aria-label="채팅 메시지">
        {messages.length === 0 && <p className="py-8 text-center text-sm text-muted">아직 메시지가 없어요. 첫 인사를 남겨보세요!</p>}
        {messages.map((m) => {
          const mine = m.username === user?.username;
          const time = new Date(m.created_at).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
          if (m.kind === "donation") {
            return (
              <div key={m.id} className="flex justify-center">
                <span className="rounded-full bg-up/10 px-3 py-1.5 text-center text-sm text-text"><b>{m.username}</b> {m.body.replace(/^💝\s*/, "💝 ")}</span>
              </div>
            );
          }
          return (
            <div key={m.id} className={`flex flex-col ${mine ? "items-end" : "items-start"}`}>
              <span className="mb-0.5 text-xs text-muted">
                {mine ? "나" : (
                  <button className="font-semibold text-text/80 underline-offset-2 hover:underline" title="선물하기"
                    onClick={() => { if (user) { setTipTo(m.username); setSheet({ kind: "tip", to: m.username }); } }}>{m.username}</button>
                )} · {time}
              </span>
              {m.kind === "donation_box" && m.box ? <BoxCard box={current(m.box)} me={user?.username} onGive={(b) => setSheet({ kind: "give", box: b })} />
                : <p className={`max-w-[85%] whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-[15px] leading-relaxed ${mine ? "bg-sky-600/35" : "bg-panel2"}`}>{m.body}</p>}
            </div>
          );
        })}
      </div>

      {sheet && user && (
        <div className="mt-3 space-y-2 rounded-2xl bg-panel2 p-3" role="dialog" aria-label="기부">
          <div className="flex items-center justify-between">
            <b className="text-sm">{sheet.kind === "open" ? "🎁 기부함 만들기" : sheet.kind === "give" ? `🎁 '${sheet.box.title}'에 기부` : "💝 선물하기"}</b>
            <button className="text-sm text-muted" onClick={() => setSheet(null)} aria-label="닫기">✕</button>
          </div>
          {sheet.kind === "open" ? (
            <>
              <input className="input font-sans" maxLength={60} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="무엇을 위해 모으나요? (예: 상장비 모아요)" aria-label="기부함 제목" />
              <label className="block text-xs text-muted">목표 금액 (최대 500,000 · 하루 1번)</label>
            </>
          ) : sheet.kind === "tip" ? (
            <input className="input font-sans" value={tipTo} onChange={(e) => setTipTo(e.target.value)} placeholder="받는 사람 아이디" aria-label="받는 사람" />
          ) : <Progress box={current(sheet.box)} />}
          <input className="input" inputMode="numeric" value={amount} onChange={(e) => setAmount(e.target.value.replace(/\D/g, ""))} aria-label="금액" />
          <div className="flex gap-1.5">
            {(sheet.kind === "open" ? [10_000, 50_000, 100_000, 500_000] : QUICK).map((q) => (
              <button key={q} type="button" onClick={() => setAmount(String(q))} className="chip flex-1 px-2 py-1.5 text-xs">{credits(q)}</button>
            ))}
          </div>
          {sheet.kind !== "open" && <input className="input font-sans" maxLength={100} value={note} onChange={(e) => setNote(e.target.value)} placeholder="응원 메시지 (선택)" aria-label="메시지" />}
          <button className="btn-primary w-full py-2.5" disabled={sending || amt <= 0 || (sheet.kind === "open" && title.trim().length < 2) || (sheet.kind === "tip" && !tipTo.trim())}
            onClick={() => {
              if (sheet.kind === "open") call(() => api(`/api/chat/${room}/boxes`, { method: "POST", body: { title, goal: amt } }), "기부함을 열었어요");
              else if (sheet.kind === "give") call(() => api(`/api/donation-boxes/${sheet.box.id}/give`, { method: "POST", body: { amount: amt, message: note || null } }), "기부했어요 💝");
              else call(() => api(`/api/chat/${room}/tip`, { method: "POST", body: { to: tipTo.trim(), amount: amt, message: note || null } }), "선물했어요 💝");
            }}>
            {sending ? "처리 중…" : sheet.kind === "open" ? `₩${credits(amt)} 목표로 열기` : `₩${credits(amt)} 보내기`}
          </button>
        </div>
      )}

      {user ? (
        <form onSubmit={send} className="mt-3 flex gap-2">
          <div className="relative">
            <button type="button" className="btn-ghost h-full px-3 text-lg" aria-label="기부 메뉴"
              onClick={() => setSheet(sheet ? null : { kind: "open" })}>＋</button>
          </div>
          <input className="input flex-1 font-sans text-[15px]" value={text} maxLength={300} onChange={(e) => setText(e.target.value)}
            placeholder="메시지 입력" aria-label="채팅 메시지 입력" />
          <button className="btn-primary px-4" disabled={sending || !text.trim()}>전송</button>
        </form>
      ) : <p className="mt-3 text-center text-sm text-muted">채팅에 참여하려면 로그인하세요.</p>}
      {user && !sheet && (
        <div className="mt-2 flex gap-2 text-xs">
          <button className="chip px-3 py-1.5 text-xs" onClick={() => setSheet({ kind: "open" })}>🎁 기부함 만들기</button>
          <button className="chip px-3 py-1.5 text-xs" onClick={() => { setTipTo(""); setSheet({ kind: "tip", to: "" }); }}>💝 선물하기</button>
        </div>
      )}
      {error && <p role="alert" className="mt-2 text-sm text-red-300">⚠ {error}</p>}
      {notice && !error && <p role="status" className="mt-2 text-sm text-emerald-300">✓ {notice}</p>}
      <p className="mt-2 text-xs text-muted">이름을 누르면 선물할 수 있어요. 기부는 가입 3일·체결 5회 이후부터, 하루 보내기·받기 각각 500,000까지예요. 모든 금액은 현금 가치가 없는 게임 크레딧입니다.</p>
    </div>
  );
}
