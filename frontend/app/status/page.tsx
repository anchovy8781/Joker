"use client";

import { Card, Empty, ErrorBox } from "@/components/ui";
import { dateTime, timeAgo } from "@/lib/format";
import { useApi } from "@/lib/hooks";

type JobInfo = { status: string; finished_at: string | null; error: string | null } | null;
type Status = {
  server_time: string;
  database: string;
  youtube_api_configured: boolean;
  data_sources: { youtube_api: number; demo: number };
  stale_channels: number;
  halted_assets: number;
  last_jobs: Record<string, JobInfo>;
  last_reconciliation: { status: string; at: string } | null;
  fee_bps: number;
  disclaimer: string;
};

const JOB_LABEL: Record<string, string> = {
  collect_data: "채널 데이터 수집", price_update: "일일 가격 산정", reconcile: "원장 대사",
  risk_scan: "이상 거래 탐지", mm_requote: "유동성 공급 호가 갱신",
};

export default function StatusPage() {
  const { data, error } = useApi<Status>("/api/system/status", 15_000);
  return (
    <div className="space-y-4">
      <ErrorBox error={error} />
      {!data ? <Empty>불러오는 중…</Empty> : (
        <>
          <div className="grid gap-3 md:grid-cols-4">
            <Card title="데이터베이스"><p>{data.database === "ok" ? "● 정상" : "⚠ 이상"}</p></Card>
            <Card title="YouTube API"><p>{data.youtube_api_configured ? "● 설정됨" : "○ 미설정 (데모 데이터만 갱신)"}</p></Card>
            <Card title="오래된 데이터 채널"><p>{data.stale_channels > 0 ? `⚠ ${data.stale_channels}개` : "● 없음"}</p></Card>
            <Card title="거래 정지 종목"><p>{data.halted_assets > 0 ? `⏸ ${data.halted_assets}개` : "● 없음"}</p></Card>
          </div>
          <Card title="데이터 출처">
            <p className="text-sm">실제 데이터(YouTube API) 종목 <b>{data.data_sources.youtube_api}</b>개 · 데모 데이터 종목 <b>{data.data_sources.demo}</b>개</p>
          </Card>
          <Card title="백그라운드 작업">
            <table className="table">
              <thead><tr><th>작업</th><th>마지막 결과</th><th>완료 시각</th></tr></thead>
              <tbody>
                {Object.entries(data.last_jobs).map(([k, j]) => (
                  <tr key={k}>
                    <td>{JOB_LABEL[k] ?? k}</td>
                    <td>{!j ? "○ 실행 기록 없음" : j.status === "succeeded" ? "● 성공" : `✕ 실패 ${j.error ?? ""}`}</td>
                    <td className="text-muted">{j?.finished_at ? `${dateTime(j.finished_at)} (${timeAgo(j.finished_at)})` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-xs text-muted">마지막 원장 대사: {data.last_reconciliation ? `${data.last_reconciliation.status === "ok" ? "● 일치" : "⚠ 불일치"} · ${dateTime(data.last_reconciliation.at)}` : "기록 없음"}</p>
          </Card>
          <p className="text-xs text-muted">{data.disclaimer} 거래 수수료 {data.fee_bps / 100}%. 서버 시각 {dateTime(data.server_time)}</p>
        </>
      )}
    </div>
  );
}
