"use client";

import { useState } from "react";

const HUES = [350, 20, 45, 150, 190, 215, 260, 290];

function hue(seed: string): number {
  let h = 0;
  for (const ch of seed) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return HUES[h % HUES.length];
}

/** Real YouTube channel thumbnail; falls back to a coloured initial tile (demo channels or a
 *  failed image load) so every row still reads at a glance. */
export function ChannelAvatar({ src, title, seed, size = 48, className = "" }: {
  src: string | null | undefined; title: string; seed: string; size?: number; className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const style = { width: size, height: size };
  if (src && !failed) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img src={src} alt="" width={size} height={size} loading="lazy" referrerPolicy="no-referrer"
        onError={() => setFailed(true)} style={style}
        className={`shrink-0 rounded-xl bg-panel2 object-cover ${className}`} />
    );
  }
  const name = title.replace(/^\[DEMO\]\s*/, "").trim();
  const h = hue(seed);
  return (
    <div aria-hidden style={{ ...style, background: `linear-gradient(135deg, hsl(${h} 70% 55%), hsl(${(h + 40) % 360} 65% 35%))` }}
      className={`flex shrink-0 items-center justify-center rounded-xl font-bold text-white ${className}`}>
      <span style={{ fontSize: size * 0.42 }}>{name.slice(0, 1) || "?"}</span>
    </div>
  );
}
