// A faint line-art Simorgh, for the quiet background of a showcase panel:
// the login page's right half, the portal's hero, and the band on the landing
// page where the particles take the bird's shape.
//
// It is decorative, so it carries no accessible name and never takes a click.
// Colour comes from CSS variables, so a caller can retune it per surface
// (`--simorgh-line`, `--simorgh-glow`) with a style attribute or a utility
// class; it falls back to the brand's indigo and cyan.

import { SIMORGH_LINES } from "./simorgh-geometry";
import { cn } from "cn";

export type SimorghLinesProps = {
  className?: string;
  /** 0…1, how strongly the strokes show. */
  opacity?: number;
};

export function SimorghLines({ className, opacity = 0.16 }: SimorghLinesProps) {
  return (
    <svg
      viewBox="0 0 220 220"
      aria-hidden
      focusable="false"
      className={cn("pointer-events-none select-none", className)}
      fill="none"
      stroke="var(--simorgh-line, #818cf8)"
      strokeLinecap="round"
      strokeLinejoin="round"
      opacity={opacity}
    >
      {/* the infinity the wings trace, a touch brighter than the bird */}
      <path d={SIMORGH_LINES.infinity} strokeWidth={1.6} stroke="var(--simorgh-glow, #22d3ee)" strokeOpacity={0.75} />
      {SIMORGH_LINES.strokes.map((d, i) => (
        <path key={i} d={d} strokeWidth={i < 2 ? 1.5 : 1.1} />
      ))}
      {SIMORGH_LINES.eyes.map((e, i) => (
        <ellipse
          key={i}
          cx={e.cx}
          cy={e.cy}
          rx={e.rx}
          ry={e.ry}
          transform={`rotate(${e.rot} ${e.cx} ${e.cy})`}
          strokeWidth={1}
          stroke="var(--simorgh-glow, #22d3ee)"
        />
      ))}
    </svg>
  );
}
