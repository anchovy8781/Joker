"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type ChatMessage } from "@/lib/api";
import { useAuth } from "./AuthProvider";

/** Polling chat for one room ("global" or "asset:<id>"). Messages render as plain text. */
export function ChatBox({ room, height = "h-[55vh]" }: { room: string; height?: string }) {
  const { user } = useAuth();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const lastId = useRef(0);
  const listRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  const load = useCallback(async () => {
    try {
      const fresh = await api<ChatMessage[]>(`/api/chat/${room}/messages${lastId.current ? `?after_id=${lastId.current}` : ""}`);
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
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (stick.current && listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [messages]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim() || sending) return;
    setSending(true);
    setError(null);
    try {
      await api(`/api/chat/${room}/messages`, { method: "POST", body: { body: text } });
      setText("");
      stick.current = true;
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "전송 실패");
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="flex flex-col">
      <div ref={listRef} className={`${height} space-y-2 overflow-y-auto pr-1`}
        onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40; }}
        role="log" aria-live="polite" aria-label="채팅 메시지">
        {messages.length === 0 && <p className="py-8 text-center text-sm text-muted">아직 메시지가 없습니다. 첫 인사를 남겨보세요!</p>}
        {messages.map((m) => {
          const mine = m.username === user?.username;
          return (
            <div key={m.id} className={`flex flex-col ${mine ? "items-end" : "items-start"}`}>
              <span className="text-[11px] text-muted">{mine ? "나" : m.username} · {new Date(m.created_at).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })}</span>
              <p className={`max-w-[85%] whitespace-pre-wrap break-words rounded-2xl px-3 py-2 text-sm ${mine ? "bg-sky-600/30" : "bg-panel2"}`}>{m.body}</p>
            </div>
          );
        })}
      </div>
      {user ? (
        <form onSubmit={send} className="mt-3 flex gap-2">
          <input className="input flex-1" value={text} maxLength={300} onChange={(e) => setText(e.target.value)}
            placeholder="메시지 입력 (최대 300자)" aria-label="채팅 메시지 입력" />
          <button className="btn-primary px-4" disabled={sending || !text.trim()}>전송</button>
        </form>
      ) : <p className="mt-3 text-center text-sm text-muted">채팅에 참여하려면 로그인하세요.</p>}
      {error && <p role="alert" className="mt-2 text-xs text-red-300">⚠ {error}</p>}
      <p className="mt-2 text-[11px] text-muted">투자 권유·광고·욕설은 숨김 처리될 수 있습니다. 모든 거래는 현금 가치가 없는 게임 크레딧입니다.</p>
    </div>
  );
}
