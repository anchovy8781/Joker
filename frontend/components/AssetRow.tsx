import Link from "next/link";
import type { Asset } from "@/lib/api";
import { credits, pct } from "@/lib/format";
import { ChannelAvatar } from "./ChannelAvatar";

export const channelName = (a: Asset) => a.channel.title.replace(/^\[DEMO\]\s*/, "");

export function ChangeText({ percent, className = "" }: { percent: number; className?: string }) {
  const color = percent > 0 ? "text-up" : percent < 0 ? "text-down" : "text-muted";
  return <span className={`font-semibold tabular-nums ${color} ${className}`}>{pct(percent)}</span>;
}

/** One stock as a tappable card row: thumbnail · name · symbol | price · change. */
export function AssetRow({ a, rank, fav, onFav, right }: {
  a: Asset; rank?: number; fav?: boolean; onFav?: (id: number) => void; right?: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-3 rounded-3xl bg-panel px-4 py-3">
      {rank !== undefined && <span className="w-5 text-center text-lg font-bold italic text-muted">{rank}</span>}
      <Link href={`/assets/${a.id}`} className="flex min-w-0 flex-1 items-center gap-3">
        <ChannelAvatar src={a.channel.thumbnail_url} title={a.channel.title} seed={a.symbol} size={52} />
        <div className="min-w-0">
          <div className="truncate text-[15px] font-bold text-text">{channelName(a)}</div>
          <div className="flex items-center gap-1.5 truncate text-sm text-muted">
            <span className="font-mono">{a.symbol}</span>
            {a.is_demo && <span className="rounded bg-accent/15 px-1 text-[11px] font-bold text-accent">DEMO</span>}
            {a.trading_status !== "active" && <span className="rounded bg-zinc-500/20 px-1 text-[11px] font-bold text-zinc-300">정지</span>}
            {a.fee_bps > 20 && <span title="할증 수수료 적용 중">🔥</span>}
          </div>
        </div>
      </Link>
      {onFav && (
        <button onClick={() => onFav(a.id)} aria-label={fav ? "즐겨찾기 해제" : "즐겨찾기"} aria-pressed={fav}
          className={`px-1 text-xl ${fav ? "text-accent" : "text-muted/60 hover:text-muted"}`}>{fav ? "★" : "☆"}</button>
      )}
      {right ?? (
        <Link href={`/assets/${a.id}`} className="text-right">
          <div className="font-bold tabular-nums">₩{credits(a.current_price)}</div>
          <ChangeText percent={a.change_pct} className="text-sm" />
        </Link>
      )}
    </div>
  );
}
