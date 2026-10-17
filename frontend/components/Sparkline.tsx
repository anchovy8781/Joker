/** Tiny trend line for list rows and cards. Colour follows the first→last direction. */
export function Sparkline({ points, width = 120, height = 36, className = "" }: {
  points: number[]; width?: number; height?: number; className?: string;
}) {
  if (points.length < 2) return <div style={{ width, height }} className={className} />;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const x = (i: number) => (i / (points.length - 1)) * width;
  const y = (v: number) => 2 + (1 - (v - min) / span) * (height - 4);
  const d = points.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const up = points[points.length - 1] >= points[0];
  const color = up ? "#f04452" : "#3d8bfd";
  const id = `sp${points.length}${Math.round(points[0])}${Math.round(points[points.length - 1])}`;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} className={className} aria-hidden>
      <defs>
        <linearGradient id={id} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${d} L${width},${height} L0,${height} Z`} fill={`url(#${id})`} />
      <path d={d} fill="none" stroke={color} strokeWidth="1.8" strokeLinejoin="round" />
    </svg>
  );
}
