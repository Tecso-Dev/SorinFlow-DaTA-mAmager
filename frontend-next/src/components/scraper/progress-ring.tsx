"use client";

// The jobs table's own 3D touch: a small extruded ring per row instead of a
// flat bar — the same stacked-layer language as PageHeader's IsoBadge, at
// table scale. A conic-gradient fill, so twenty rows cost nothing and it
// reads the same under `prefers-reduced-motion` (no animation loop, only a
// CSS transition on the fill itself).

import { faNum } from "@/lib/format";

export function ProgressRing({ value, size = 34 }: { value: number; size?: number }) {
  const clamped = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
  return (
    <div
      className="relative shrink-0"
      style={{ width: size, height: size }}
      role="img"
      aria-label={`پیشرفت ${faNum(Math.round(clamped))} درصد`}
    >
      <div
        className="absolute inset-0 rounded-full shadow-[0_3px_8px_-3px_rgb(99_102_241/0.55)] transition-[background] duration-700"
        style={{ background: `conic-gradient(var(--primary) ${clamped * 3.6}deg, var(--muted) 0deg)` }}
      />
      <div className="absolute inset-[3px] rounded-full bg-card" />
      <div className="absolute inset-0 grid place-items-center">
        <span className="text-[9px] font-bold tabular">{faNum(Math.round(clamped))}</span>
      </div>
    </div>
  );
}
