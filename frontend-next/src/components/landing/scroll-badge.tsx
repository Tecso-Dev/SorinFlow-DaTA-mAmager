"use client";

// The page's scroll indicator: a disc at the bottom-left whose ring IS the
// brand name, with the percentage of the whole page in the middle. Deliberately
// not a bar across the top of the page — this is the only progress readout.
// Clicking it goes back to the top.

import { ArrowUp } from "lucide-react";
import { motion, useMotionValueEvent, useReducedMotion, useScroll, useTransform } from "motion/react";
import { useState } from "react";
import { cn } from "cn";
import { faNum } from "@/lib/format";

const R = 41; // the text ring's radius inside a 0 0 100 100 box
const CIRC = 2 * Math.PI * R;
// clockwise from the top, so Latin letters read left-to-right along the top
const RING = `M50 ${50 - R} A${R} ${R} 0 1 1 49.99 ${50 - R}`;

export function ScrollBadge({ brandLatin }: { brandLatin: string }) {
  const { scrollYProgress } = useScroll();
  const still = useReducedMotion();
  const [pct, setPct] = useState(0);

  useMotionValueEvent(scrollYProgress, "change", (v) => {
    const next = Math.max(0, Math.min(100, Math.round(v * 100)));
    setPct((prev) => (prev === next ? prev : next));
  });

  // The ring turns as the page moves; a third of a turn over the whole page is
  // enough to feel alive without making the name unreadable.
  const spin = useTransform(scrollYProgress, [0, 1], [0, 120]);

  // One word per third of the circle, stretched to the exact circumference so
  // there is never a gap or an overlap whatever the brand is called.
  const word = (brandLatin || "SorinFlow").toUpperCase();
  const ring = `${word} • ${word} • ${word} • `;
  const atTop = pct < 2;

  return (
    <div className="pointer-events-none fixed bottom-4 left-4 z-40 sm:bottom-6 sm:left-6">
      <button
        type="button"
        onClick={() => window.scrollTo({ top: 0, behavior: still ? "auto" : "smooth" })}
        disabled={atTop}
        aria-label={`${faNum(pct)} درصد صفحه پیمایش شده — بازگشت به بالای صفحه`}
        className={cn(
          "group pointer-events-auto relative grid size-[72px] place-items-center rounded-full outline-none transition sm:size-[92px]",
          "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          atTop ? "cursor-default" : "hover:scale-[1.06]",
        )}
      >
        <span
          aria-hidden
          className="absolute inset-[9%] rounded-full border bg-background/80 shadow-[0_10px_30px_-12px_rgb(49_46_129/0.6)] backdrop-blur-xl dark:bg-[#07070d]/80 dark:shadow-[0_14px_40px_-14px_rgb(0_0_0/0.9)]"
        />
        <svg viewBox="0 0 100 100" className="absolute inset-0 size-full overflow-visible" aria-hidden>
          <defs>
            <path id="sf-scroll-ring" d={RING} fill="none" />
            <linearGradient id="sf-scroll-arc" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stopColor="#818cf8" />
              <stop offset="0.5" stopColor="#a78bfa" />
              <stop offset="1" stopColor="#22d3ee" />
            </linearGradient>
          </defs>
          {/* the track, and over it the arc the page has covered so far */}
          <circle cx="50" cy="50" r={R - 7.5} fill="none" strokeWidth="2.5" className="stroke-foreground/12" />
          <motion.circle
            cx="50"
            cy="50"
            r={R - 7.5}
            fill="none"
            stroke="url(#sf-scroll-arc)"
            strokeWidth="2.5"
            strokeLinecap="round"
            transform="rotate(-90 50 50)"
            style={{ pathLength: scrollYProgress }}
          />
          <motion.g style={still ? undefined : { rotate: spin, originX: "50px", originY: "50px" }}>
            <text
              className="fill-muted-foreground text-[8.4px] font-black tracking-[0.18em] transition group-hover:fill-foreground"
              style={{ letterSpacing: "0.18em" }}
            >
              <textPath href="#sf-scroll-ring" textLength={CIRC} lengthAdjust="spacing" startOffset="0">
                {ring}
              </textPath>
            </text>
          </motion.g>
        </svg>
        <span aria-hidden className="relative flex flex-col items-center leading-none">
          {atTop ? (
            <span className="text-[11px] font-black text-muted-foreground tabular-nums sm:text-sm">۰٪</span>
          ) : (
            <>
              <span className="bg-linear-to-l from-indigo-500 via-violet-500 to-cyan-500 bg-clip-text text-[13px] font-black text-transparent tabular-nums sm:text-lg dark:from-indigo-300 dark:via-violet-300 dark:to-cyan-300">
                {faNum(pct)}٪
              </span>
              <ArrowUp className="mt-0.5 size-3 text-muted-foreground opacity-0 transition group-hover:opacity-100 group-focus-visible:opacity-100" />
            </>
          )}
        </span>
      </button>
    </div>
  );
}
