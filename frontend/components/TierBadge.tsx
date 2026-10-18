const COLORS: Record<string, string> = {
  브론즈: "bg-[#8a5a3b]/25 text-[#e0a47a]", 실버: "bg-zinc-400/20 text-zinc-200", 골드: "bg-amber-400/20 text-amber-300",
  플래티넘: "bg-teal-400/20 text-teal-200", 다이아: "bg-sky-400/20 text-sky-200", 마스터: "bg-fuchsia-500/20 text-fuchsia-200",
};

export function TierBadge({ tier, className = "" }: { tier: string; className?: string }) {
  return <span className={`rounded-full px-2 py-0.5 text-xs font-bold ${COLORS[tier] ?? COLORS["브론즈"]} ${className}`}>{tier}</span>;
}
