import Link from "next/link";
import type { ReactNode } from "react";
import type { Freshness } from "@/lib/api";
import { direction, pct, signed, timeAgo } from "@/lib/format";

export function Card({ title, right, children, className = "" }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-xl border border-line bg-panel ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold text-text">{title}</h2>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

/** Up/down shown with colour AND arrow AND sign, so it never relies on colour alone. */
export function Change({ value, percent, className = "" }: { value?: number; percent: number; className?: string }) {
  const d = direction(percent);
  const color = d === "up" ? "text-up" : d === "down" ? "text-down" : "text-muted";
  const icon = d === "up" ? "▲" : d === "down" ? "▼" : "■";
  const label = d === "up" ? "상승" : d === "down" ? "하락" : "보합";
  return (
    <span className={`font-mono tabular-nums ${color} ${className}`} aria-label={`${label} ${pct(percent)}`}>
      <span aria-hidden>{icon}</span> {value !== undefined && <>{signed(value)} </>}({pct(percent)})
    </span>
  );
}

export function PnL({ value }: { value: number | string }) {
  const v = typeof value === "string" ? Number(value) : value;
  const d = direction(v);
  const color = d === "up" ? "text-up" : d === "down" ? "text-down" : "text-muted";
  return (
    <span className={`font-mono tabular-nums ${color}`}>
      <span aria-hidden>{d === "up" ? "▲ " : d === "down" ? "▼ " : ""}</span>
      {signed(v)}
    </span>
  );
}

export function DemoBadge({ isDemo }: { isDemo: boolean }) {
  return isDemo ? (
    <span title="데모 데이터 공급자가 생성한 가상 지표입니다. 실제 유튜브 데이터가 아닙니다."
      className="rounded border border-accent/60 bg-accent/10 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-accent">
      DEMO 데이터
    </span>
  ) : (
    <span title="YouTube Data API 공식 데이터" className="rounded border border-sky-500/60 bg-sky-500/10 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-sky-300">
      YouTube API
    </span>
  );
}

export function FreshnessBadge({ f }: { f: Freshness }) {
  const map = {
    fresh: { cls: "border-emerald-500/50 text-emerald-300", icon: "●", text: "최신" },
    stale: { cls: "border-red-500/60 text-red-300", icon: "⚠", text: "오래된 데이터" },
    never: { cls: "border-zinc-500/60 text-zinc-300", icon: "○", text: "수집 전" },
  }[f.status];
  return (
    <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] ${map.cls}`}
      title={`마지막 데이터 갱신: ${f.last_update_at ?? "없음"} (기준 ${f.stale_after_hours}시간)`}>
      <span aria-hidden>{map.icon}</span>
      {map.text} · {timeAgo(f.last_update_at)}
    </span>
  );
}

export function TradingBadge({ status, reason }: { status: string; reason?: string | null }) {
  if (status === "active") return <span className="rounded border border-emerald-500/40 px-1.5 py-0.5 text-[11px] text-emerald-300">● 거래 가능</span>;
  return (
    <span className="rounded border border-red-500/60 bg-red-500/10 px-1.5 py-0.5 text-[11px] text-red-300" title={reason ?? ""}>
      ⏸ 거래 정지
    </span>
  );
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="rounded-xl border border-line bg-panel p-4">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-1 font-mono text-xl font-semibold tabular-nums text-text">{value}</div>
      {sub && <div className="mt-1 text-xs">{sub}</div>}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return <div role="alert" className="rounded-lg border border-red-500/50 bg-red-500/10 px-3 py-2 text-sm text-red-200">⚠ {msg}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="py-6 text-center text-sm text-muted">{children}</div>;
}

export function LoginPrompt() {
  return (
    <Card>
      <div className="py-8 text-center">
        <p className="text-muted">로그인이 필요합니다.</p>
        <div className="mt-4 flex justify-center gap-2">
          <Link href="/login" className="btn-primary">로그인</Link>
          <Link href="/register" className="btn-ghost">회원가입</Link>
        </div>
      </div>
    </Card>
  );
}
