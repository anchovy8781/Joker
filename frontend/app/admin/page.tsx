"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { ChannelAvatar } from "@/components/ChannelAvatar";
import { ChannelPicker } from "@/components/ChannelPicker";
import type { ChannelPreviewData } from "@/components/ChannelPreview";
import { Card, Empty, ErrorBox } from "@/components/ui";
import { api, ApiError, type Freshness } from "@/lib/api";
import { compact, credits, dateTime } from "@/lib/format";
import { useApi } from "@/lib/hooks";

type AdminChannel = { id: number; youtube_channel_id: string; title: string; handle: string | null; thumbnail_url: string | null; data_source: string; listing_status: string; data_quality_status: string; freshness: Freshness; requested_by: string | null; request_note: string | null; asset: { id: number; symbol: string; trading_status: string; halt_reason: string | null; reference_price: number; current_price?: number; celebrity_until: string | null; new_video_until: string | null } | null };
type ChatRow = { id: number; room: string; user_id: number; username: string; body: string; created_at: string; hidden_at: string | null; hidden_reason: string | null };
type RiskEv = { id: number; user_id: number | null; username: string | null; asset_id: number | null; event_type: string; severity: string; reason: string; evidence: unknown; created_at: string; reviewed_at: string | null; resolution: string | null };
type Audit = { id: number; actor_id: number | null; action: string; target_type: string | null; target_id: string | null; request_id: string | null; metadata: unknown; created_at: string };
type JobRow = { id: number; job_type: string; status: string; attempts: number; finished_at: string | null; last_error: string | null; result: unknown };
type Recon = { id: number; status: string; checked_accounts: number; mismatches: unknown[]; totals: { total_cash?: number; fees_burned?: number }; created_at: string };
type PU = { id: number; symbol: string; effective_date: string; status: string; old_price: number; new_price: number; error: string | null };
type Ticket = { id: number; status: string; reason?: string; message?: string; target_type?: string; target_id?: string | null; user_id?: number; created_at: string; resolution: string | null };

const TABS = [["overview", "운영 현황"], ["listing", "상장 승인"], ["users", "사용자"], ["settings", "⚙ 설정"], ["chat", "채팅 관리"], ["risk", "위험 이벤트"], ["audit", "감사 로그"], ["jobs", "작업·가격 갱신"], ["recon", "원장 대사"], ["tickets", "신고·이의"], ["notice", "공지"]] as const;

export default function AdminPage() {
  const { user, ready } = useAuth();
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("overview");
  useEffect(() => {
    // Deep link from notifications, e.g. /admin?tab=listing (remote approval from the phone app).
    const t = new URLSearchParams(window.location.search).get("tab");
    if (t && TABS.some(([k]) => k === t)) setTab(t as (typeof TABS)[number][0]);
  }, []);
  if (!ready) return <Empty>…</Empty>;
  if (user?.role !== "admin") return <Card><Empty>관리자 권한이 필요합니다.</Empty></Card>;
  return (
    <div className="space-y-4">
      <div className="no-scrollbar -mx-4 flex gap-1.5 overflow-x-auto px-4">
        {TABS.map(([k, l]) => <button key={k} onClick={() => setTab(k)} className={`chip px-3 py-1.5 ${tab === k ? "chip-on" : ""}`}>{l}</button>)}
      </div>
      <p className="text-xs text-muted">모든 관리자 조치는 관리자 ID·사유·대상·시각과 함께 감사 로그에 기록됩니다. 거래·원장 기록은 수정/삭제할 수 없습니다.</p>
      {tab === "overview" && <Overview go={(t) => setTab(t)} />}
      {tab === "listing" && <Listing />}
      {tab === "users" && <Users />}
      {tab === "settings" && <SettingsTab />}
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
  // One touch: no dialog. A standard reason still goes to the audit log.
  return `관리자 ${label}`;
}

function Listing() {
  const { data, reload } = useApi<AdminChannel[]>("/api/admin/channels", 10_000);
  const [busy, setBusy] = useState<number | null>(null);
  const [msg, setMsg] = useState<{ id: number; text: string } | null>(null);
  const [preview, setPreview] = useState<ChannelPreviewData | null>(null);
  const [pickKey, setPickKey] = useState(0);
  const [confirmDelist, setConfirmDelist] = useState<number | null>(null);

  const rows = data ?? [];
  const pending = rows.filter((c) => c.listing_status === "pending");
  const listed = rows.filter((c) => c.listing_status === "listed");
  const tap = async (id: number, path: string, done: string, body: unknown = {}) => {
    setBusy(id);
    setMsg(null);
    try { await api(path, { method: "POST", body }); setMsg({ id, text: `✓ ${done}` }); reload(); }
    catch (e) { setMsg({ id, text: `⚠ ${e instanceof ApiError ? e.message : "실패했어요. 다시 눌러 주세요"}` }); }
    finally { setBusy(null); }
  };

  return (
    <div className="space-y-4">
      <section>
        <h2 className="mb-2 text-lg font-bold">상장 요청 {pending.length > 0 && <span className="text-up">{pending.length}</span>}</h2>
        {msg && <p role="status" className="mb-2 rounded-xl bg-panel2 px-3 py-2 text-sm">{msg.text}</p>}
        {!data ? <Empty>불러오는 중…</Empty> : pending.length === 0 ? <Card><Empty>새 상장 요청이 없어요</Empty></Card> : (
          <ul className="space-y-2">
            {pending.map((c) => (
              <li key={c.id} className="rounded-3xl bg-panel p-4">
                <div className="flex items-center gap-3">
                  <ChannelAvatar src={c.thumbnail_url} title={c.title} seed={c.youtube_channel_id} size={48} className="shrink-0 rounded-full" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-bold">{c.title}</div>
                    <div className="truncate text-sm text-muted">{c.handle ?? c.youtube_channel_id}{c.requested_by && <> · {c.requested_by}</>}</div>
                  </div>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <button className="btn-primary py-3 text-base" disabled={busy !== null}
                    onClick={() => tap(c.id, `/api/admin/channels/${c.id}/approve`, `'${c.title}' 상장했어요`)}>
                    {busy === c.id ? "처리 중…" : "승인"}
                  </button>
                  <button className="btn-danger py-3 text-base" disabled={busy !== null}
                    onClick={() => tap(c.id, `/api/admin/channels/${c.id}/reject`, `'${c.title}' 거부했어요 (신청 비용 환불)`)}>
                    거부
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <details className="rounded-3xl bg-panel p-4">
        <summary className="cursor-pointer font-bold">상장된 종목 {listed.length}개 · 거래 켜기/끄기 · 상장폐지</summary>
        <p className="mt-2 text-xs text-muted">상장폐지하면 거래가 영구히 멈추고, 모든 주문이 취소되며, 보유자에게 마지막 체결가로 정산돼요. 되돌릴 수 없어요.</p>
        <ul className="mt-2 divide-y divide-line">
          {listed.map((c) => c.asset && (
            <li key={c.id} className="flex items-center gap-3 py-2">
              <ChannelAvatar src={c.thumbnail_url} title={c.title} seed={c.youtube_channel_id} size={36} className="shrink-0 rounded-full" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-bold">{c.title}</div>
                <div className="truncate font-mono text-xs text-muted">{c.asset.symbol} · ₩{credits(c.asset.current_price ?? c.asset.reference_price)}</div>
              </div>
              <button role="switch" aria-checked={c.asset.trading_status === "active"} disabled={busy !== null}
                onClick={() => tap(c.id, `/api/admin/assets/${c.asset!.id}/${c.asset!.trading_status === "active" ? "halt" : "resume"}`,
                  c.asset!.trading_status === "active" ? `${c.asset!.symbol} 거래 정지` : `${c.asset!.symbol} 거래 재개`)}
                className={`shrink-0 whitespace-nowrap rounded-full px-3 py-2 text-sm font-semibold ${c.asset.trading_status === "active" ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/15 text-red-300"}`}>
                {c.asset.trading_status === "active" ? "● 거래 중" : "○ 정지"}
              </button>
              <button className="shrink-0 rounded-full px-2 py-2 text-xs font-semibold text-muted hover:text-text" disabled={busy !== null}
                title="채널 이름으로 읽기 쉬운 심볼을 만들어요 (예: 보따 → BOTTA)"
                onClick={() => tap(c.id, `/api/admin/assets/${c.asset!.id}/symbol`, "심볼을 정리했어요", {})}>심볼 정리</button>
              {confirmDelist === c.id ? (
                <span className="flex shrink-0 gap-1">
                  <button className="btn-danger px-3" disabled={busy !== null}
                    onClick={async () => { setConfirmDelist(null); await tap(c.id, `/api/admin/assets/${c.asset!.id}/delist`, `${c.asset!.symbol} 상장폐지 · 보유자 정산 완료`, { reason: "관리자 상장폐지" }); }}>
                    폐지 확정
                  </button>
                  <button className="btn-ghost px-3" onClick={() => setConfirmDelist(null)}>취소</button>
                </span>
              ) : (
                <button className="shrink-0 rounded-full px-2 py-2 text-xs font-semibold text-muted hover:text-up" disabled={busy !== null}
                  onClick={() => setConfirmDelist(c.id)}>상장폐지</button>
              )}
            </li>
          ))}
        </ul>
      </details>

      <details className="rounded-3xl bg-panel p-4">
        <summary className="cursor-pointer font-bold">채널 직접 상장하기</summary>
        <div className="mt-3">
          <ChannelPicker key={pickKey} onPicked={setPreview} />
          {preview && (
            <button className="btn-primary mt-3 w-full py-3 text-base" disabled={busy !== null || preview.listing_status === "listed"}
              onClick={async () => { await tap(-1, "/api/admin/quick-list", `'${preview.title}' 상장했어요`, { channel: preview.youtube_channel_id }); setPreview(null); setPickKey((k) => k + 1); }}>
              {preview.listing_status === "listed" ? "이미 상장된 채널" : `'${preview.title}' 상장`}
            </button>
          )}
        </div>
      </details>
    </div>
  );
}

type OverviewData = { users: { total: number; new_24h: number; active_24h: number; restricted: number }; user_cash: number;
  trades_24h: number; turnover_24h: number; player_trades_24h: number; listed: number; halted: number;
  pending_requests: number; open_reports: number; open_risk_events: number };

function Overview({ go }: { go: (t: (typeof TABS)[number][0]) => void }) {
  const { data, reload } = useApi<OverviewData>("/api/admin/overview", 15_000);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [confirmHalt, setConfirmHalt] = useState(false);
  const act = async (path: string, done: string) => {
    setBusy(true); setMsg(null);
    try { await api(path, { method: "POST", body: { reason: "관리자 원터치 처리" } }); setMsg(`✓ ${done}`); reload(); }
    catch (e) { setMsg(`⚠ ${e instanceof ApiError ? e.message : "실패했어요"}`); }
    finally { setBusy(false); setConfirmHalt(false); }
  };
  if (!data) return <Empty>불러오는 중…</Empty>;
  const tile = (label: string, value: string, sub?: string, onClick?: () => void) => (
    <button type="button" onClick={onClick} disabled={!onClick}
      className="rounded-3xl bg-panel p-4 text-left disabled:cursor-default">
      <div className="text-sm text-muted">{label}</div>
      <div className="mt-1 text-xl font-bold tabular-nums">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted">{sub}</div>}
    </button>
  );
  return (
    <div className="space-y-4">
      {msg && <p role="status" className="rounded-xl bg-panel2 px-3 py-2 text-sm">{msg}</p>}
      {(data.pending_requests > 0 || data.open_reports > 0 || data.open_risk_events > 0) && (
        <div className="grid gap-2 sm:grid-cols-3">
          {data.pending_requests > 0 && <button className="btn-primary py-3" onClick={() => go("listing")}>상장 요청 {data.pending_requests}건 처리하기</button>}
          {data.open_reports > 0 && <button className="btn-ghost py-3" onClick={() => go("tickets")}>신고 {data.open_reports}건 보기</button>}
          {data.open_risk_events > 0 && <button className="btn-ghost py-3" onClick={() => go("risk")}>위험 이벤트 {data.open_risk_events}건</button>}
        </div>
      )}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        {tile("전체 사용자", `${credits(data.users.total)}명`, `24시간 신규 ${data.users.new_24h} · 접속 ${data.users.active_24h}`, () => go("users"))}
        {tile("사용자 보유 현금", `₩${compact(data.user_cash)}`, "가상 크레딧 합계")}
        {tile("24시간 체결", `${credits(data.trades_24h)}건`, `사람 참여 ${credits(data.player_trades_24h)}건`)}
        {tile("24시간 거래대금", `₩${compact(data.turnover_24h)}`)}
        {tile("상장 종목", `${data.listed}개`, data.halted ? `정지 ${data.halted}개` : "모두 거래 중", () => go("listing"))}
        {tile("이용 제한 사용자", `${data.users.restricted}명`, undefined, () => go("users"))}
      </div>
      <div className="rounded-3xl bg-panel p-4">
        <div className="font-bold">전체 시장</div>
        <p className="mt-1 text-sm text-muted">긴급 상황에서 모든 종목의 거래를 한 번에 멈추거나 다시 열어요.</p>
        <div className="mt-3 grid grid-cols-2 gap-2">
          {confirmHalt ? (
            <button className="btn-danger py-3" disabled={busy} onClick={() => act("/api/admin/market/halt-all", "전체 거래 정지")}>정말 전체 정지</button>
          ) : (
            <button className="btn-danger py-3" disabled={busy} onClick={() => setConfirmHalt(true)}>전체 거래 정지</button>
          )}
          <button className="btn-primary py-3" disabled={busy} onClick={() => act("/api/admin/market/resume-all", "전체 거래 재개")}>전체 거래 재개</button>
        </div>
      </div>
    </div>
  );
}

type UserRow = { id: number; username: string; role: string; status: string; cash: number; holdings_value: number; equity: number;
  created_at: string; last_login_at: string | null };

function Users() {
  const [q, setQ] = useState("");
  const { data, reload } = useApi<UserRow[]>(`/api/admin/users${q.trim() ? `?q=${encodeURIComponent(q.trim())}` : ""}`);
  const [open, setOpen] = useState<number | null>(null);
  const [amount, setAmount] = useState(100_000);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const run = async (path: string, body: unknown, done: string) => {
    setBusy(true); setMsg(null);
    try { await api(path, { method: "POST", body }); setMsg(`✓ ${done}`); reload(); }
    catch (e) { setMsg(`⚠ ${e instanceof ApiError ? e.message : "실패했어요"}`); }
    finally { setBusy(false); }
  };
  const statusLabel: Record<string, string> = { active: "정상", restricted: "제한", suspended: "정지" };
  return (
    <div className="space-y-3">
      <input className="input" placeholder="아이디로 검색" value={q} onChange={(e) => setQ(e.target.value)} />
      {msg && <p role="status" className="rounded-xl bg-panel2 px-3 py-2 text-sm">{msg}</p>}
      {!data ? <Empty>불러오는 중…</Empty> : data.length === 0 ? <Card><Empty>사용자가 없어요</Empty></Card> : (
        <ul className="space-y-2">
          {data.map((u) => (
            <li key={u.id} className="rounded-2xl bg-panel p-3">
              <button type="button" className="flex w-full items-center gap-3 text-left" onClick={() => setOpen(open === u.id ? null : u.id)}>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-bold">{u.username} {u.role === "admin" && <span className="text-xs text-primary">관리자</span>}</div>
                  <div className="text-xs text-muted">가입 {dateTime(u.created_at)} · 최근 접속 {u.last_login_at ? dateTime(u.last_login_at) : "—"}</div>
                </div>
                <div className="text-right">
                  <div className="font-bold tabular-nums">₩{compact(u.equity)}</div>
                  <div className={`text-xs ${u.status === "active" ? "text-muted" : "text-up"}`}>{statusLabel[u.status] ?? u.status}</div>
                </div>
              </button>
              {open === u.id && (
                <div className="mt-3 space-y-3 border-t border-line pt-3 text-sm">
                  <div className="text-muted">현금 ₩{credits(u.cash)} · 보유 주식 평가 ₩{credits(u.holdings_value)}</div>
                  <div>
                    <div className="mb-1.5 font-semibold">크레딧 지급 / 회수</div>
                    <div className="flex flex-wrap gap-1.5">
                      {[10_000, 100_000, 1_000_000].map((v) => (
                        <button key={v} className={`chip px-3 py-1.5 ${amount === v ? "chip-on" : ""}`} onClick={() => setAmount(v)}>₩{compact(v)}</button>
                      ))}
                    </div>
                    <div className="mt-2 grid grid-cols-2 gap-2">
                      <button className="btn-primary py-2.5" disabled={busy}
                        onClick={() => run("/api/admin/adjustments", { user_id: u.id, amount, reason: `관리자 지급 (${u.username})`,
                          idempotency_key: crypto.randomUUID().replace(/-/g, "").slice(0, 32) }, `${u.username}에게 ₩${credits(amount)} 지급`)}>지급</button>
                      <button className="btn-danger py-2.5" disabled={busy || u.cash < amount}
                        onClick={() => run("/api/admin/adjustments", { user_id: u.id, amount: -amount, reason: `관리자 회수 (${u.username})`,
                          idempotency_key: crypto.randomUUID().replace(/-/g, "").slice(0, 32) }, `${u.username}에게서 ₩${credits(amount)} 회수`)}>회수</button>
                    </div>
                  </div>
                  {u.role === "user" && (
                    <div>
                      <div className="mb-1.5 font-semibold">이용 상태</div>
                      <div className="grid grid-cols-3 gap-2">
                        {(["active", "restricted", "suspended"] as const).map((st) => (
                          <button key={st} disabled={busy || u.status === st}
                            className={`rounded-xl py-2.5 font-semibold ${u.status === st ? "bg-text text-bg" : "bg-panel2 text-text"}`}
                            onClick={() => run(`/api/admin/users/${u.id}/status`, { status: st }, `${u.username} → ${statusLabel[st]}`)}>
                            {statusLabel[st]}
                          </button>
                        ))}
                      </div>
                      <p className="mt-1 text-xs text-muted">제한: 매수·매도 불가 · 정지: 로그인 불가</p>
                    </div>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

type SettingRow = { key: string; label: string; help: string; kind: "bool" | "choice"; choices: number[]; value: boolean | number; default: boolean | number };

function fmtChoice(key: string, v: number): string {
  if (key === "fee_bps") return `${v / 100}%`;
  if (key === "bot_interval_seconds") return `${v}초`;
  if (key === "bot_persona_interval_seconds") return v >= 60 ? `${v / 60}분` : `${v}초`;
  if (key === "listing_requests_per_day") return `${v}개`;
  return v === 0 ? "무료" : `₩${credits(v)}`;
}

function SettingsTab() {
  const { data, reload } = useApi<SettingRow[]>("/api/admin/settings");
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const set = async (key: string, value: boolean | number) => {
    setBusy(key);
    setMsg(null);
    try { await api(`/api/admin/settings/${key}`, { method: "POST", body: { value } }); reload(); }
    catch (e) { setMsg(`⚠ ${e instanceof ApiError ? e.message : "실패"}`); }
    finally { setBusy(null); }
  };
  if (!data) return <Empty>불러오는 중…</Empty>;
  return (
    <div className="space-y-2">
      {msg && <p role="alert" className="text-sm">{msg}</p>}
      {data.map((r) => (
        <div key={r.key} className="rounded-3xl bg-panel p-5">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="font-bold">{r.label}</div>
              <div className="text-sm text-muted">{r.help}</div>
            </div>
            {r.kind === "bool" && (
              <button role="switch" aria-checked={!!r.value} aria-label={r.label} disabled={busy === r.key}
                onClick={() => set(r.key, !r.value)}
                className={`relative h-7 w-12 shrink-0 rounded-full transition-colors ${r.value ? "bg-emerald-500" : "bg-zinc-600"}`}>
                <span className={`absolute top-1 h-5 w-5 rounded-full bg-white transition-all ${r.value ? "left-6" : "left-1"}`} />
              </button>
            )}
          </div>
          {r.kind === "choice" && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {r.choices.map((c) => (
                <button key={c} disabled={busy === r.key} onClick={() => set(r.key, c)}
                  className={`chip px-3 py-1.5 text-sm ${r.value === c ? "chip-on" : ""}`}>{fmtChoice(r.key, c)}</button>
              ))}
            </div>
          )}
        </div>
      ))}
      <p className="text-xs text-muted">바꾼 설정은 몇 초 안에 전체에 적용되고, 누가 언제 바꿨는지 감사 로그에 남아요.</p>
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
  const jobs = useApi<JobRow[]>("/api/admin/jobs", 10_000);
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
