"use client";

import { useState } from "react";
import { api, ApiError, type PriceAlert } from "@/lib/api";
import { credits } from "@/lib/format";
import { useApi } from "@/lib/hooks";

/** "Tell me when it gets to X" — notifications fire once, checked every minute. */
export function PriceAlerts({ assetId, current }: { assetId: number; current: number }) {
  const list = useApi<PriceAlert[]>(`/api/price-alerts?asset_id=${assetId}`, 30_000);
  const [price, setPrice] = useState(String(Math.round(current * 1.05)));
  const [msg, setMsg] = useState<string | null>(null);
  const target = Number(price) || 0;
  const direction = target >= current ? "above" : "below";

  async function add() {
    setMsg(null);
    try {
      await api("/api/price-alerts", { method: "POST", body: { asset_id: assetId, direction, target_price: target } });
      setMsg(`✓ ${credits(target)} ${direction === "above" ? "이상" : "이하"}이 되면 알려드릴게요`);
      list.reload();
    } catch (e) {
      setMsg(e instanceof ApiError ? `⚠ ${e.message}` : "⚠ 실패했어요");
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex gap-1.5">
        {[-10, -5, 5, 10].map((d) => (
          <button key={d} type="button" className="chip flex-1 px-2 py-1.5 text-xs"
            onClick={() => setPrice(String(Math.max(1, Math.round(current * (1 + d / 100)))))}>{d > 0 ? `+${d}` : d}%</button>
        ))}
      </div>
      <div className="flex gap-2">
        <input className="input flex-1" inputMode="numeric" value={price} aria-label="알림 가격"
          onChange={(e) => setPrice(e.target.value.replace(/\D/g, ""))} />
        <button className="btn-primary whitespace-nowrap px-3" disabled={!target || target === current} onClick={add}>
          {direction === "above" ? "▲ 이상" : "▼ 이하"} 알림
        </button>
      </div>
      {msg && <p className="text-sm">{msg}</p>}
      {list.data && list.data.length > 0 && (
        <ul className="space-y-1.5">
          {list.data.map((a) => (
            <li key={a.id} className="flex items-center justify-between rounded-xl bg-panel2 px-3 py-2 text-sm">
              <span>{a.direction === "above" ? "▲" : "▼"} ₩{credits(a.target_price)} {a.direction === "above" ? "이상" : "이하"}
                {!a.active && <span className="ml-1 text-xs text-emerald-300">· 도달함</span>}</span>
              {a.active && <button className="text-xs text-muted underline" onClick={async () => {
                await api(`/api/price-alerts/${a.id}/cancel`, { method: "POST" }); list.reload();
              }}>해제</button>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
