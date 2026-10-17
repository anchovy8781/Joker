"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { Card, Empty, ErrorBox, FreshnessBadge } from "@/components/ui";
import { api, ApiError, type Freshness } from "@/lib/api";
import { credits, dateTime } from "@/lib/format";
import { useApi } from "@/lib/hooks";

type AdminChannel = { id: number; youtube_channel_id: string; title: string; data_source: string; listing_status: string; data_quality_status: string; freshness: Freshness; requested_by: string | null; request_note: string | null; asset: { id: number; symbol: string; trading_status: string; halt_reason: string | null; reference_price: number; celebrity_until: string | null; new_video_until: string | null } | null };
type ChatRow = { id: number; room: string; user_id: number; username: string; body: string; created_at: string; hidden_at: string | null; hidden_reason: string | null };
type RiskEv = { id: number; user_id: number | null; username: string | null; asset_id: number | null; event_type: string; severity: string; reason: string; evidence: unknown; created_at: string; reviewed_at: string | null; resolution: string | null };
type Audit = { id: number; actor_id: number | null; action: string; target_type: string | null; target_id: string | null; request_id: string | null; metadata: unknown; created_at: string };
type JobRow = { id: number; job_type: string; status: string; attempts: number; finished_at: string | null; last_error: string | null; result: unknown };
type Recon = { id: number; status: string; checked_accounts: number; mismatches: unknown[]; totals: { total_cash?: number; fees_burned?: number }; created_at: string };
type PU = { id: number; symbol: string; effective_date: string; status: string; old_price: number; new_price: number; error: string | null };
type Ticket = { id: number; status: string; reason?: string; message?: string; target_type?: string; target_id?: string | null; user_id?: number; created_at: string; resolution: string | null };

const TABS = [["listing", "상장 관리"], ["chat", "채팅 관리"], ["risk", "위험 이벤트"], ["audit", "감사 로그"], ["jobs", "작업·가격 갱신"], ["recon", "원장 대사"], ["tickets", "신고·이의"], ["notice", "공지"]] as const;

export default function AdminPage() {
  const { user, ready } = useAuth();
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("listing");
  useEffect(() => {
    // Deep link from notifications, e.g. /admin?tab=listing (remote approval from the phone app).
    const t = new URLSearchParams(window.location.search).get("tab");
    if (t && TABS.some(([k]) => k === t)) setTab(t as (typeof TABS)[number][0]);
  }, []);
  if (!ready) return <Empty>…</Empty>;
  if (user?.role !== "admin") return <Card><Empty>관리자 권한이 필요합니다.</Empty></Card>;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-1">
        {TABS.map(([k, l]) => <button key={k} onClick={() => setTab(k)} className={`rounded px-3 py-1.5 text-sm ${tab === k ? "bg-panel2 text-text" : "text-muted"}`}>{l}</button>)}
      </div>
      <p className="text-xs text-muted">모든 관리자 조치는 관리자 ID·사유·대상·시각과 함께 감사 로그에 기록됩니다. 거래·원장 기록은 수정/삭제할 수 없습니다.</p>
      {tab === "listing" && <Listing />}
      {tab === "risk" && <Risk />}
      {tab === "audit" && <AuditTab />}
      {tab === "jobs" && <Jobs />}
      {tab === "recon" && <ReconTab />}
      {tab === "tickets" && <Tickets />}
      {tab === "notice" && <Notice />}
      {tab === "chat" && <ChatModeration />}
    </div>
  );
}

function useAction() {
  const [msg, setMsg] = useState<string | null>(null);
  const run = async (path: string, body?: unknown, after?: () => void) => {
    setMsg(null);
    try { await api(path, { method: "POST", body: body ?? {} }); setMsg("✓ 완료"); after?.(); }
    catch (e) { setMsg(`⚠ ${e instanceof ApiError ? e.message : "실패"}`); }
  };
  return { msg, run };
}

function askReason(label: string): string | null {
  const r = window.prompt(`${label} 사유를 입력하세요 (필수, 감사 로그에 기록)`);
  return r && r.trim().length >= 3 ? r.trim() : null;
}

function Listing() {
  const { data, reload } = useApi<AdminChannel[]>("/api/admin/channels");
  const { msg, run } = useAction();
  const [form, setForm] = useState({ youtube_channel_id: "", title: "", data_source: "youtube_api" });
  const [approve, setApprove] = useState<Record<number, { symbol: string; price: string; shares: string; celebrity: boolean }>>({});
  const rows = data ? [...data].sort((a, b) => Number(b.listing_status === "pending") - Number(a.listing_status === "pending")) : null;
  return (
    <div className="space-y-4">
      <Card title="채널 등록 (상장 심사 대기열에 추가)">
        <div className="grid gap-2 md:grid-cols-4">
          <input className="input" placeholder="@핸들, 채널 주소 또는 ID" value={form.youtube_channel_id} onChange={(e) => setForm({ ...form, youtube_channel_id: e.target.value })} />
          <input className="input" placeholder="표시 이름 (실제 채널은 자동)" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
          <select className="input" value={form.data_source} onChange={(e) => setForm({ ...form, data_source: e.target.value })}>
            <option value="youtube_api">실제 (YouTube API)</option>
            <option value="demo">데모 데이터</option>
          </select>
          <button className="btn-primary" onClick={() => run("/api/admin/channels", { ...form, title: form.title.trim() || null }, reload)}>등록</button>
        </div>
        <p className="mt-2 text-xs text-muted">핸들만 넣으면 채널 ID와 이름을 자동으로 찾아요. 승인할 때 심볼·가격·주식 수를 비워두면 구독자 수 기준 추천값으로 상장돼요. 실제 채널은 첫 API 수집으로 데이터가 검증되기 전까지 거래 정지 상태로 상장됩니다.</p>
      </Card>
      {msg && <p className="text-sm">{msg}</p>}
      <Card title="채널 / 종목">
        {!rows ? <Empty>…</Empty> : (
          <div className="-mx-4 overflow-x-auto px-4"><table className="table">
            <thead><tr><th>채널</th><th>출처</th><th>상장</th><th>데이터</th><th>종목</th><th>조치</th></tr></thead>
            <tbody>{rows.map((c) => {
              const ap = approve[c.id] ?? { symbol: "", price: "", shares: "", celebrity: false };
              return (
                <tr key={c.id}>
                  <td><div className="max-w-[220px] truncate">{c.title}</div><div className="font-mono text-[11px] text-muted">{c.youtube_channel_id}</div>
                    {c.requested_by && <div className="text-[11px] text-sky-300">요청: {c.requested_by}{c.request_note && ` · ${c.request_note}`}</div>}
                    {c.asset?.celebrity_until && <div className="text-[11px] text-orange-300">🔥 유명인 신규 채널 할증</div>}</td>
                  <td>{c.data_source === "demo" ? "DEMO" : "YouTube API"}</td>
                  <td>{c.listing_status}</td>
                  <td><FreshnessBadge f={c.freshness} /> <span className="text-xs text-muted">{c.data_quality_status}</span></td>
                  <td>{c.asset ? <span className="font-mono">{c.asset.symbol} · {credits(c.asset.reference_price)} · {c.asset.trading_status}</span> : "—"}
                    {c.asset?.halt_reason && <div className="max-w-[220px] truncate text-[11px] text-red-300">{c.asset.halt_reason}</div>}</td>
                  <td className="space-x-1">
                    {c.listing_status === "pending" && (
                      <span className="inline-flex flex-wrap gap-1">
                        <input className="input w-20 py-1" placeholder="심볼 자동" value={ap.symbol} onChange={(e) => setApprove({ ...approve, [c.id]: { ...ap, symbol: e.target.value.toUpperCase() } })} />
                        <input className="input w-24 py-1" placeholder="가격 자동" value={ap.price} onChange={(e) => setApprove({ ...approve, [c.id]: { ...ap, price: e.target.value } })} title="최초 가격" />
                        <input className="input w-28 py-1" placeholder="주식 수 자동" value={ap.shares} onChange={(e) => setApprove({ ...approve, [c.id]: { ...ap, shares: e.target.value } })} title="발행 주식 수" />
                        <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={ap.celebrity} onChange={(e) => setApprove({ ...approve, [c.id]: { ...ap, celebrity: e.target.checked } })} />유명인 신규</label>
                        <button className="btn-primary py-1" onClick={() => { const r = askReason("상장 승인"); if (r) run(`/api/admin/channels/${c.id}/approve`, { symbol: ap.symbol || null, initial_price: ap.price ? Number(ap.price) : null, shares_outstanding: ap.shares ? Number(ap.shares) : null, reason: r, celebrity_new_channel: ap.celebrity }, reload); }}>승인</button>
                        <button className="btn-danger py-1" onClick={() => { const r = askReason("상장 거절"); if (r) run(`/api/admin/channels/${c.id}/reject`, { reason: r }, reload); }}>거절</button>
                      </span>
                    )}
                    {c.asset && c.asset.trading_status === "active" && <button className="btn-danger py-1" onClick={() => { const r = askReason("거래 정지"); if (r) run(`/api/admin/assets/${c.asset!.id}/halt`, { reason: r }, reload); }}>거래 정지</button>}
                    {c.asset && c.asset.trading_status === "halted" && <button className="btn-ghost py-1" onClick={() => { const r = askReason("거래 재개"); if (r) run(`/api/admin/assets/${c.asset!.id}/resume`, { reason: r }, reload); }}>재개</button>}
                  </td>
                </tr>
              );
            })}</tbody>
          </table></div>
        )}
      </Card>
    </div>
  );
}

function Risk() {
  const [onlyOpen, setOnlyOpen] = useState(true);
  const { data, reload } = useApi<RiskEv[]>(`/api/admin/risk-events?${onlyOpen ? "reviewed=false&" : ""}limit=200`);
  const { msg, run } = useAction();
  return (
    <Card title="위험 이벤트" right={<label className="text-xs"><input type="checkbox" checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} /> 미검토만</label>}>
      {msg && <p className="mb-2 text-sm">{msg}</p>}
      {!data?.length ? <Empty>이벤트 없음</Empty> : (
        <div className="-mx-4 overflow-x-auto px-4"><table className="table">
          <thead><tr><th>#</th><th>시각</th><th>심각도</th><th>유형</th><th>사용자</th><th>사유</th><th>처리</th></tr></thead>
          <tbody>{data.map((e) => (
            <tr key={e.id}>
              <td>{e.id}</td><td className="text-muted">{dateTime(e.created_at)}</td>
              <td className={e.severity === "critical" || e.severity === "high" ? "text-red-300" : ""}>{e.severity}</td>
              <td>{e.event_type}</td><td>{e.username ?? (e.asset_id ? `asset ${e.asset_id}` : "—")}</td>
              <td className="max-w-[320px] whitespace-normal text-xs" title={JSON.stringify(e.evidence)}>{e.reason}</td>
              <td>{e.reviewed_at ? <span className="text-xs text-muted">{e.resolution}</span> : (
                <span className="space-x-1">
                  <button className="btn-ghost py-0.5 text-xs" onClick={() => { const r = askReason("오탐 처리"); if (r) run(`/api/admin/risk-events/${e.id}/review`, { decision: "dismiss", resolution: r }, reload); }}>오탐</button>
                  <button className="btn-danger py-0.5 text-xs" onClick={() => { const r = askReason("악용 확인"); if (r) run(`/api/admin/risk-events/${e.id}/review`, { decision: "confirm", resolution: r }, reload); }}>확인</button>
                  {e.user_id && <button className="btn-danger py-0.5 text-xs" onClick={() => { const r = askReason("계정 제한"); if (r) run(`/api/admin/users/${e.user_id}/status`, { status: "restricted", reason: r }, reload); }}>제한</button>}
                </span>
              )}</td>
            </tr>))}
          </tbody>
        </table></div>
      )}
    </Card>
  );
}

function AuditTab() {
  const [action, setAction] = useState("");
  const { data } = useApi<Audit[]>(`/api/admin/audit-logs?limit=200${action ? `&action=${encodeURIComponent(action)}` : ""}`);
  return (
    <Card title="감사 로그 (추가 전용)" right={<input className="input w-48 py-1" placeholder="action 접두어 (예: asset.)" value={action} onChange={(e) => setAction(e.target.value)} />}>
      {!data?.length ? <Empty>기록 없음</Empty> : (
        <div className="-mx-4 overflow-x-auto px-4"><table className="table text-xs">
          <thead><tr><th>#</th><th>시각</th><th>행위자</th><th>action</th><th>대상</th><th>내용</th></tr></thead>
          <tbody>{data.map((a) => (
            <tr key={a.id}><td>{a.id}</td><td className="text-muted">{dateTime(a.created_at)}</td><td>{a.actor_id ?? "system"}</td><td className="font-mono">{a.action}</td>
              <td>{a.target_type} {a.target_id}</td><td className="max-w-[420px] truncate font-mono text-[11px] text-muted" title={JSON.stringify(a.metadata)}>{JSON.stringify(a.metadata)}</td></tr>))}
          </tbody>
        </table></div>
      )}
    </Card>
  );
}

function Jobs() {
  const jobs = useApi<JobRow[]>("/api/admin/jobs", 5_000);
  const pus = useApi<PU[]>("/api/admin/price-updates?limit=50");
  const { msg, run } = useAction();
  return (
    <div className="space-y-4">
      <Card title="작업 수동 실행" right={msg && <span className="text-xs">{msg}</span>}>
        <div className="flex flex-wrap gap-2">
          {["collect_data", "price_update", "reconcile", "risk_scan", "mm_requote"].map((j) => (
            <button key={j} className="btn-ghost" onClick={() => run(`/api/admin/jobs/${j}/run`, undefined, jobs.reload)}>{j}</button>
          ))}
        </div>
        <p className="mt-2 text-xs text-muted">price_update는 날짜별로 한 번만 큐에 들어가며, 동일 날짜 재실행 시 가격은 중복 적용되지 않습니다. 워커(`python -m app.worker`)가 실행 중이어야 처리됩니다.</p>
      </Card>
      <Card title="최근 작업">
        {!jobs.data?.length ? <Empty>없음</Empty> : (
          <div className="-mx-4 overflow-x-auto px-4"><table className="table text-xs">
            <thead><tr><th>#</th><th>작업</th><th>상태</th><th>시도</th><th>완료</th><th>오류/결과</th></tr></thead>
            <tbody>{jobs.data.map((j) => (
              <tr key={j.id}><td>{j.id}</td><td>{j.job_type}</td><td className={j.status === "failed" ? "text-red-300" : ""}>{j.status}</td><td>{j.attempts}</td>
                <td className="text-muted">{dateTime(j.finished_at)}</td><td className="max-w-[400px] truncate font-mono text-[11px]">{j.last_error ?? JSON.stringify(j.result)}</td></tr>))}
            </tbody>
          </table></div>
        )}
      </Card>
      <Card title="가격 갱신 기록 (실패·보류 포함)">
        {!pus.data?.length ? <Empty>없음</Empty> : (
          <table className="table text-xs">
            <thead><tr><th>날짜</th><th>종목</th><th>상태</th><th className="num">이전 → 신규</th><th>사유</th></tr></thead>
            <tbody>{pus.data.map((p) => (
              <tr key={p.id}><td>{p.effective_date}</td><td className="font-mono">{p.symbol}</td><td className={p.status !== "applied" ? "text-amber-300" : ""}>{p.status}</td>
                <td className="num">{credits(p.old_price)} → {credits(p.new_price)}</td><td className="text-muted">{p.error ?? ""}</td></tr>))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

function ReconTab() {
  const { data, reload } = useApi<Recon[]>("/api/admin/reconciliation");
  const { msg, run } = useAction();
  return (
    <Card title="원장 대사 결과" right={<button className="btn-ghost" onClick={() => run("/api/admin/reconciliation/run", undefined, reload)}>지금 실행</button>}>
      {msg && <p className="mb-2 text-sm">{msg}</p>}
      {!data?.length ? <Empty>실행 기록 없음</Empty> : (
        <table className="table text-xs">
          <thead><tr><th>#</th><th>시각</th><th>결과</th><th className="num">계좌 수</th><th className="num">총 현금</th><th className="num">소각된 수수료</th><th>불일치</th></tr></thead>
          <tbody>{data.map((r) => (
            <tr key={r.id}><td>{r.id}</td><td className="text-muted">{dateTime(r.created_at)}</td><td className={r.status === "ok" ? "text-emerald-300" : "text-red-300"}>{r.status === "ok" ? "● 일치" : "⚠ 불일치"}</td>
              <td className="num">{r.checked_accounts}</td><td className="num">{credits(r.totals.total_cash)}</td><td className="num">{credits(r.totals.fees_burned)}</td>
              <td className="max-w-[300px] truncate font-mono text-[11px]">{r.mismatches.length ? JSON.stringify(r.mismatches) : "—"}</td></tr>))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function Tickets() {
  const reports = useApi<Ticket[]>("/api/admin/reports");
  const appeals = useApi<Ticket[]>("/api/admin/appeals");
  const { msg, run } = useAction();
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card title="신고">
        {!reports.data?.length ? <Empty>없음</Empty> : reports.data.map((r) => (
          <div key={r.id} className="mb-2 rounded bg-panel2 p-2 text-xs">
            <b>#{r.id}</b> [{r.status}] {r.target_type} {r.target_id} — {r.reason}
            {r.status === "open" && <span className="ml-2 space-x-1">
              <button className="btn-ghost py-0.5 text-xs" onClick={() => { const x = askReason("처리 완료"); if (x) run(`/api/admin/reports/${r.id}/resolve`, { status: "resolved", resolution: x }, reports.reload); }}>처리</button>
              <button className="btn-ghost py-0.5 text-xs" onClick={() => { const x = askReason("기각"); if (x) run(`/api/admin/reports/${r.id}/resolve`, { status: "dismissed", resolution: x }, reports.reload); }}>기각</button>
            </span>}
          </div>
        ))}
      </Card>
      <Card title="이의 제기">
        {msg && <p className="mb-2 text-sm">{msg}</p>}
        {!appeals.data?.length ? <Empty>없음</Empty> : appeals.data.map((a) => (
          <div key={a.id} className="mb-2 rounded bg-panel2 p-2 text-xs">
            <b>#{a.id}</b> [{a.status}] user {a.user_id} — {a.message}
            {a.status === "open" && <span className="ml-2 space-x-1">
              <button className="btn-ghost py-0.5 text-xs" onClick={() => { const x = askReason("이의 수용"); if (x) run(`/api/admin/appeals/${a.id}/resolve`, { status: "accepted", resolution: x }, appeals.reload); }}>수용</button>
              <button className="btn-danger py-0.5 text-xs" onClick={() => { const x = askReason("이의 기각"); if (x) run(`/api/admin/appeals/${a.id}/resolve`, { status: "rejected", resolution: x }, appeals.reload); }}>기각</button>
            </span>}
          </div>
        ))}
      </Card>
    </div>
  );
}

function Notice() {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const { msg, run } = useAction();
  return (
    <Card title="공지사항 등록">
      <input className="input" placeholder="제목" value={title} onChange={(e) => setTitle(e.target.value)} />
      <textarea className="input mt-2 h-28" placeholder="내용" value={body} onChange={(e) => setBody(e.target.value)} />
      <button className="btn-primary mt-2" disabled={!title || !body} onClick={() => run("/api/admin/announcements", { title, body }, () => { setTitle(""); setBody(""); })}>등록</button>
      {msg && <p className="mt-2 text-sm">{msg}</p>}
      <ErrorBox error={null} />
    </Card>
  );
}

function ChatModeration() {
  const { data, reload } = useApi<ChatRow[]>("/api/admin/chat?limit=200", 10_000);
  const { msg, run } = useAction();
  return (
    <Card title="채팅 관리">
      {msg && <p className="mb-2 text-sm">{msg}</p>}
      {!data?.length ? <Empty>메시지 없음</Empty> : (
        <ul className="divide-y divide-line text-sm">
          {data.map((m) => (
            <li key={m.id} className={`flex items-start gap-2 py-2 ${m.hidden_at ? "opacity-50" : ""}`}>
              <div className="min-w-0 flex-1">
                <div className="text-xs text-muted">#{m.id} · {m.room} · {m.username} · {dateTime(m.created_at)}</div>
                <p className="whitespace-pre-wrap break-words">{m.body}</p>
                {m.hidden_at && <div className="text-xs text-red-300">숨김: {m.hidden_reason}</div>}
              </div>
              {!m.hidden_at && <button className="btn-danger shrink-0 py-0.5 text-xs" onClick={() => { const r = askReason("메시지 숨김"); if (r) run(`/api/admin/chat/${m.id}/hide`, { reason: r }, reload); }}>숨김</button>}
              <button className="btn-ghost shrink-0 py-0.5 text-xs" onClick={() => { const r = askReason("작성자 제한"); if (r) run(`/api/admin/users/${m.user_id}/status`, { status: "restricted", reason: r }, reload); }}>제한</button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
