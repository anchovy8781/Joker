import Link from "next/link";
import type { Asset } from "@/lib/api";
import { compact, credits } from "@/lib/format";
import { Change, DemoBadge, Empty, FreshnessBadge, TradingBadge } from "./ui";

export function AssetTable({ assets, compactMode = false }: { assets: Asset[]; compactMode?: boolean }) {
  if (assets.length === 0) return <Empty>조건에 맞는 종목이 없습니다</Empty>;
  return (
    <div className="-mx-4 overflow-x-auto px-4">
      <table className="table">
        <thead>
          <tr>
            <th>종목</th>
            <th className="num">현재가</th>
            <th className="num">등락</th>
            {!compactMode && <th className="num">24h 거래량</th>}
            {!compactMode && <th>상태</th>}
            {!compactMode && <th>데이터</th>}
          </tr>
        </thead>
        <tbody>
          {assets.map((a) => (
            <tr key={a.id} className="hover:bg-panel2">
              <td>
                <Link href={`/assets/${a.id}`} className="flex items-center gap-2">
                  <span className="font-mono font-semibold text-text">{a.symbol}</span>
                  <span className="max-w-[180px] truncate text-xs text-muted">{a.channel.title}</span>
                  <DemoBadge isDemo={a.is_demo} />
                </Link>
              </td>
              <td className="num">{credits(a.current_price)}</td>
              <td className="num"><Change percent={a.change_pct} /></td>
              {!compactMode && <td className="num">{compact(a.volume_24h)}</td>}
              {!compactMode && <td><TradingBadge status={a.trading_status} reason={a.halt_reason} /></td>}
              {!compactMode && <td><FreshnessBadge f={a.freshness} /></td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
