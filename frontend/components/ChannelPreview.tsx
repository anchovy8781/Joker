import Link from "next/link";
import { compact } from "@/lib/format";
import { ChannelAvatar } from "./ChannelAvatar";

export type ChannelPreviewData = {
  youtube_channel_id: string; title: string; handle: string | null; thumbnail_url: string | null;
  subscriber_count: number | null; view_count: number | null; video_count: number | null;
  subscriber_text: string | null; hidden_subscriber_count: boolean; source: string | null;
  listing_status: string | null; asset_id?: number | null; listing_fee?: number;
};

const STATUS: Record<string, string> = { pending: "이미 상장 심사 중", listed: "이미 상장된 채널", rejected: "이전에 거절된 채널" };

/** Profile card shown before a listing request (or a one-touch listing). */
export function ChannelPreview({ p }: { p: ChannelPreviewData }) {
  const subs = p.hidden_subscriber_count ? "구독자 비공개"
    : p.subscriber_count != null ? `구독자 ${compact(p.subscriber_count)}명` : p.subscriber_text;
  return (
    <div className="flex items-center gap-3 rounded-2xl bg-panel2 p-3">
      <ChannelAvatar src={p.thumbnail_url} title={p.title} seed={p.youtube_channel_id} size={64} className="rounded-full" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-base font-bold">{p.title}</div>
        <div className="truncate text-sm text-muted">{p.handle ?? p.youtube_channel_id}</div>
        <div className="mt-0.5 flex flex-wrap gap-x-2 text-sm">
          {subs && <span>{subs}</span>}
          {p.video_count != null && <span className="text-muted">영상 {compact(p.video_count)}개</span>}
          {p.view_count != null && <span className="text-muted">조회 {compact(p.view_count)}</span>}
        </div>
        {p.listing_status && (
          <div className="mt-1 text-sm font-semibold text-amber-300">
            ⚠ {STATUS[p.listing_status] ?? p.listing_status}
            {p.listing_status === "listed" && p.asset_id && <> · <Link className="underline" href={`/assets/${p.asset_id}`}>종목 보기</Link></>}
          </div>
        )}
      </div>
    </div>
  );
}
