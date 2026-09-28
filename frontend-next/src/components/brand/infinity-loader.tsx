"use client";

// The page-level loading state: a «شب نیلی» ∞ with a light running along it.
// Small button spinners stay as they are (a Loader2 next to the label); this
// is for a whole page or a whole shell that has nothing to show yet.
//
// `motion/react` drives the light, so it stops with the rest of the app's
// motion; under prefers-reduced-motion the ∞ is drawn still, with the light
// parked on it, and nothing animates. The markup is identical on the server
// and on the first client render, so the page hydrates cleanly either way.

import { motion, useReducedMotion } from "motion/react";
import { useId } from "react";
import { cn } from "cn";
import { LOADER } from "./simorgh-geometry";

const DASH = Math.max(18, LOADER.length * 0.16);

export type InfinityLoaderProps = {
  /** Rendered width in pixels; the art is 2:1. */
  size?: number;
  /** Said out loud by screen readers; the element is a live busy region. */
  label?: string;
  className?: string;
};

export function InfinityLoader({ size = 96, label = "در حال بارگیری", className }: InfinityLoaderProps) {
  const reduce = useReducedMotion();
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const glow = `sf-inf-${id}`;
  return (
    <div className={cn("flex flex-col items-center gap-3", className)} role="status" aria-live="polite">
      <svg width={size} height={size / 2} viewBox="0 0 120 60" aria-hidden focusable="false" className="overflow-visible">
        <defs>
          <linearGradient id={glow} x1="0" y1="0" x2="120" y2="60" gradientUnits="userSpaceOnUse">
            <stop offset="0" stopColor="#6366f1" />
            <stop offset="0.5" stopColor="#8b5cf6" />
            <stop offset="1" stopColor="#22d3ee" />
          </linearGradient>
        </defs>
        {/* the track: the whole ∞, dim */}
        <path d={LOADER.d} fill="none" stroke={`url(#${glow})`} strokeOpacity={0.28} strokeWidth={7} strokeLinecap="round" />
        {/* the light running along it */}
        <motion.path
          d={LOADER.d}
          fill="none"
          stroke={`url(#${glow})`}
          strokeWidth={7}
          strokeLinecap="round"
          strokeDasharray={`${DASH} ${LOADER.length}`}
          initial={{ strokeDashoffset: LOADER.length + DASH }}
          animate={reduce ? { strokeDashoffset: LOADER.length * 0.55 } : { strokeDashoffset: 0 }}
          transition={reduce ? { duration: 0 } : { duration: 4.2, ease: "linear", repeat: Infinity }}
        />
        <motion.path
          d={LOADER.d}
          fill="none"
          stroke="#ecfeff"
          strokeOpacity={0.85}
          strokeWidth={2.4}
          strokeLinecap="round"
          strokeDasharray={`${DASH * 0.45} ${LOADER.length}`}
          initial={{ strokeDashoffset: LOADER.length + DASH * 0.45 }}
          animate={reduce ? { strokeDashoffset: LOADER.length * 0.55 } : { strokeDashoffset: 0 }}
          transition={reduce ? { duration: 0 } : { duration: 4.2, ease: "linear", repeat: Infinity }}
        />
      </svg>
      <span className="text-xs text-muted-foreground">{label}</span>
    </div>
  );
}

/** A whole page with nothing to show yet: the ∞ centred in the viewport. */
export function PageLoader({ label }: { label?: string }) {
  return (
    <div className="grid min-h-dvh place-items-center px-6">
      <InfinityLoader size={132} label={label} />
    </div>
  );
}
