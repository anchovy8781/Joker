"use client";

import Link from "next/link";
import { useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { Card, DemoBadge, Empty, ErrorBox, LoginPrompt, PnL, Stat } from "@/components/ui";
import { api, ApiError, type Account, type LedgerEntry, type MyTrade, type Order, type Position } from "@/lib/api";
import { credits, dateTime, orderStatusLabel, reasonLabel, signed } from "@/lib/format";
import { useApi } from "@/lib/hooks";

const LEDGER_LABEL: Record<string, string> = {
  signup_grant: "가입 지급금", trade_buy: "매수 대금", trade_sell: "매도 대금", trade_fee: "거래 수수료",
  admin_adjustment: "운영 조정", correction: "정정",
};

type RiskInfo = { risk_level: string; status: string; events: { id: number; event_type: string; severity: string; reason: string; created_at: string; resolution: string | null }[]; appeals: { id: number; status: string; message: string; resolution: string | null }[] };

export default function Portfolio() {
  const { user, ready } = useAuth();
  const [tab, setTab] = useState<"positions" | "orders" | "trades" | "ledger" | "account">("positions");
  const account = useApi<Account>(user ? "/api/account" : null, 10_000);
  const positions = useApi<Position[]>(user ? "/api/account/positions" : null, 10_000);
  const orders = useApi<Order[]>(user ? "/api/orders?limit=100" : null, 10_000);
  const trades = useApi<MyTrade[]>(user ? "/api/trades?limit=100" : null);
  const ledger = useApi<LedgerEntry[]>(user ? "/api/account/ledger?limit=200" : null);
  const risk = useApi<RiskInfo>(user ? "/api/account/risk" : null);
  const [msg, setMsg] = useState<string | null>(null);

  if (!ready) return <Empty>…</Empty>;
  if (!user) return <LoginPrompt />;
  const a = account.data;

  async function cancel(id: number) {
    try {
      await api(`/api/orders/${id}/cancel`, { method: "POST" });
      orders.reload(); account.reload(); positions.reload();
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "취소 실패");
    }
  }

  return (
    <div className="space-y-4">
      {a && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat label="총자산 (평가)" value={credits(a.total_equity)} />
          <Stat label="현금 (예약 포함)" value={credits(a.cash_balance)} sub={<span className="text-muted">가용 {credits(a.available_cash)} · 예약 {credits(a.reserved_cash)}</span>} />
          <Stat label="실현 손익" value={<PnL value={a.realized_pnl} />} />
          <Stat label="미실현 손익" value={<PnL value={a.unrealized_pnl} />} sub={<span className="text-muted">평가 이익은 현금이 아닙니다</span>} />
        </div>
      )}
      <ErrorBox error={account.error ?? msg} />
      <div className="flex flex-wrap gap-1">
        {([["positions", "보유 종목"], ["orders", "주문"], ["trades", "체결 내역"], ["ledger", "원장"], ["account", "계정·이의제기"]] as const).map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)} className={`rounded px-3 py-1.5 text-sm ${tab === k ? "bg-panel2 text-text" : "text-muted"}`}>{l}</button>
        ))}
      </div>

      {tab === "positions" && (
        <Card title="보유 종목">
          {!positions.data?.length ? <Empty>보유 종목이 없습니다. <Link href="/assets" className="underline">종목 보기</Link></Empty> : (
            <div className="-mx-4 overflow-x-auto px-4"><table className="table">
              <thead><tr><th>종목</th><th className="num">수량</th><th className="num">평균 매입가</th><th className="num">현재가</th><th className="num">평가액</th><th className="num">미실현</th><th className="num">실현</th></tr></thead>
              <tbody>{positions.data.map((p) => (
                <tr key={p.asset_id}>
                  <td><Link href={`/assets/${p.asset_id}`} className="flex items-center gap-2 font-mono">{p.symbol} <DemoBadge isDemo={p.is_demo} /></Link></td>
                  <td className="num">{credits(p.quantity)}{p.reserved_quantity > 0 && <span className="text-xs text-muted"> (예약 {p.reserved_quantity})</span>}</td>
                  <td className="num">{credits(p.average_cost)}</td>
                  <td className="num">{credits(p.current_price)}</td>
                  <td className="num">{credits(p.market_value)}</td>
                  <td className="num"><PnL value={p.unrealized_pnl} /></td>
                  <td className="num"><PnL value={p.realized_pnl} /></td>
                </tr>))}
              </tbody>
            </table></div>
          )}
        </Card>
      )}

      {tab === "orders" && (
        <Card title="주문 내역">
          {!orders.data?.length ? <Empty>주문이 없습니다</Empty> : (
            <div className="-mx-4 overflow-x-auto px-4"><table className="table">
              <thead><tr><th>#</th><th>시각</th><th>구분</th><th>유형</th><th className="num">가격</th><th className="num">체결/수량</th><th>상태</th><th /></tr></thead>
              <tbody>{orders.data.map((o) => (
                <tr key={o.id}>
                  <td className="text-muted">{o.id}</td>
                  <td className="text-muted">{dateTime(o.created_at)}</td>
                  <td className={o.side === "buy" ? "text-up" : "text-down"}>{o.side === "buy" ? "▲ 매수" : "▼ 매도"}</td>
                  <td>{o.order_type === "limit" ? "지정가" : "시장가"}</td>
                  <td className="num">{o.limit_price ? credits(o.limit_price) : "—"}</td>
                  <td className="num">{o.filled_quantity}/{o.quantity}</td>
                  <td title={reasonLabel(o.status_reason)}>{orderStatusLabel(o.status)}</td>
                  <td>{(o.status === "open" || o.status === "partially_filled") && <button className="btn-ghost px-2 py-0.5 text-xs" onClick={() => cancel(o.id)}>취소</button>}</td>
                </tr>))}
              </tbody>
            </table></div>
          )}
        </Card>
      )}

      {tab === "trades" && (
        <Card title="체결 내역">
          {!trades.data?.length ? <Empty>체결 내역이 없습니다</Empty> : (
            <div className="-mx-4 overflow-x-auto px-4"><table className="table">
              <thead><tr><th>시각</th><th>종목</th><th>구분</th><th className="num">가격</th><th className="num">수량</th><th className="num">수수료</th><th className="num">실현 손익</th></tr></thead>
              <tbody>{trades.data.map((t) => (
                <tr key={t.id}>
                  <td className="text-muted">{dateTime(t.executed_at)}</td>
                  <td className="font-mono">{t.symbol}</td>
                  <td className={t.side === "buy" ? "text-up" : "text-down"}>{t.side === "buy" ? "▲ 매수" : "▼ 매도"}</td>
                  <td className="num">{credits(t.execution_price)}</td>
                  <td className="num">{credits(t.quantity)}</td>
                  <td className="num">{credits(t.fee)}</td>
                  <td className="num">{t.realized_pnl !== null ? <PnL value={t.realized_pnl} /> : "—"}</td>
                </tr>))}
              </tbody>
            </table></div>
          )}
        </Card>
      )}

      {tab === "ledger" && (
        <Card title="크레딧 원장 (변경 불가 기록)">
          {!ledger.data?.length ? <Empty>기록 없음</Empty> : (
            <div className="-mx-4 overflow-x-auto px-4"><table className="table">
              <thead><tr><th>시각</th><th>유형</th><th className="num">금액</th><th className="num">잔액</th><th>참조</th></tr></thead>
              <tbody>{ledger.data.map((e) => (
                <tr key={e.id}>
                  <td className="text-muted">{dateTime(e.created_at)}</td>
                  <td>{LEDGER_LABEL[e.entry_type] ?? e.entry_type}</td>
                  <td className={`num ${e.amount > 0 ? "text-up" : "text-down"}`}>{signed(e.amount)}</td>
                  <td className="num">{credits(e.balance_after)}</td>
                  <td className="text-xs text-muted">{e.reference_type} {e.reference_id}{e.memo && ` · ${e.memo}`}</td>
                </tr>))}
              </tbody>
            </table></div>
          )}
        </Card>
      )}

      {tab === "account" && <AccountTab risk={risk.data} reload={risk.reload} />}
    </div>
  );
}

function AccountTab({ risk, reload }: { risk: RiskInfo | null; reload: () => void }) {
  const [text, setText] = useState("");
  const [reportTarget, setReportTarget] = useState("");
  const [reportText, setReportText] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const send = async (path: string, body: unknown, done: () => void) => {
    try { await api(path, { method: "POST", body }); setMsg("접수되었습니다."); done(); reload(); }
    catch (e) { setMsg(e instanceof ApiError ? e.message : "실패"); }
  };
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card title="계정 상태 및 탐지 기록">
        {!risk ? <Empty>…</Empty> : (
          <>
            <p className="text-sm">상태: <b>{risk.status}</b> · 위험 수준: <b>{risk.risk_level}</b></p>
            <p className="mt-1 text-xs text-muted">자동 탐지 결과만으로 영구 정지하지 않습니다. 사유가 부당하면 이의를 제기할 수 있습니다.</p>
            <ul className="mt-3 space-y-2 text-xs">
              {risk.events.length === 0 && <li className="text-muted">탐지 기록이 없습니다.</li>}
              {risk.events.map((e) => (
                <li key={e.id} className="rounded bg-panel2 p-2">
                  <b>[{e.severity}]</b> {e.reason} <span className="text-muted">· {dateTime(e.created_at)}</span>
                  {e.resolution && <div className="text-muted">처리: {e.resolution}</div>}
                </li>
              ))}
            </ul>
          </>
        )}
      </Card>
      <div className="space-y-4">
        <Card title="이의 제기">
          <textarea className="input h-24" value={text} onChange={(e) => setText(e.target.value)} placeholder="상황을 설명해 주세요 (10자 이상)" />
          <button className="btn-primary mt-2" disabled={text.length < 10} onClick={() => send("/api/appeals", { message: text }, () => setText(""))}>제출</button>
          {risk?.appeals.map((a) => <div key={a.id} className="mt-2 text-xs text-muted">#{a.id} {a.status}{a.resolution && ` · ${a.resolution}`}</div>)}
        </Card>
        <Card title="신고하기">
          <input className="input" placeholder="대상 (종목 심볼 또는 사용자명)" value={reportTarget} onChange={(e) => setReportTarget(e.target.value)} />
          <textarea className="input mt-2 h-20" value={reportText} onChange={(e) => setReportText(e.target.value)} placeholder="신고 사유 (5자 이상)" />
          <button className="btn-ghost mt-2" disabled={reportText.length < 5}
            onClick={() => send("/api/reports", { target_type: "other", target_id: reportTarget || null, reason: reportText }, () => { setReportText(""); setReportTarget(""); })}>신고</button>
        </Card>
        {msg && <p className="text-sm text-muted">{msg}</p>}
      </div>
    </div>
  );
}
