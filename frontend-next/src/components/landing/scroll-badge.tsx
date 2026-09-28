"use client";

// The page's scroll indicator: a disc at the bottom-left whose outer ring IS
// the brand name, turning as the page moves, with the percentage of the whole
// page in the middle. Deliberately not a bar across the top of the page — this
// is the only progress readout. Clicking it goes back to the top.

import { ArrowUp } from "lucide-react";
import { motion, useMotionValueEvent, useScroll, useSpring, useTransform } from "motion/react";
import { useState } from "react";
import { cn } from "cn";
import { faNum } from "@/lib/format";
import { usePrefersStill } from "@/lib/use-still";

// Three rings inside a 0 0 100 100 box: the name on the outside, the progress
// arc under it, and the disc that holds the number in the middle.
const NAME_R = 44;
const ARC_R = 34;

/** The brand's name repeated around the circle, one <text> per letter.
 *
 *  Not <textPath>: it needs a <path> in <defs> and a same-document reference,
 *  and that reference silently resolves to nothing in more than one engine —
 *  every glyph then lands on the origin and the ring reads as empty. Placing
 *  each letter at its own angle has no reference to lose. */
function ringLetters(word: string) {
  const unit = `${word} • `;
  // as many whole repeats as sit comfortably around the circle
  const repeats = Math.max(1, Math.round(36 / unit.length));
  const letters = unit.repeat(repeats).split("");
  const step = 360 / letters.length;
  return letters.map((char, i) => ({ char, angle: i * step }));
}

export function ScrollBadge({ brandLatin }: { brandLatin: string }) {
  const { scrollYProgress } = useScroll();
  const still = usePrefersStill();
  const [pct, setPct] = useState(0);

  useMotionValueEvent(scrollYProgress, "change", (v) => {
    const next = Math.max(0, Math.min(100, Math.round(v * 100)));
    setPct((prev) => (prev === next ? prev : next));
  });

  // A full turn of the name over the length of the page, eased so it keeps
  // gliding for a moment after the wheel stops instead of stepping with it.
  const eased = useSpring(scrollYProgress, { stiffness: 120, damping: 30, restDelta: 0.0005 });
  const spin = useTransform(eased, [0, 1], [0, 360]);

  const ring = ringLetters((brandLatin || "SorinFlow").toUpperCase());
  const atTop = pct < 2;

  return (
    <div className="pointer-events-none fixed bottom-4 left-4 z-40 sm:bottom-6 sm:left-6">
      <button
        type="button"
        onClick={() => window.scrollTo({ top: 0, behavior: still ? "auto" : "smooth" })}
        disabled={atTop}
        aria-label={`${faNum(pct)} درصد صفحه پیمایش شده — بازگشت به بالای صفحه`}
        className={cn(
          "group pointer-events-auto relative grid size-[84px] place-items-center rounded-full outline-none transition sm:size-[108px]",
          "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          atTop ? "cursor-default" : "hover:scale-[1.06]",
        )}
      >
        {/* the disc the number sits on, well inside both rings */}
        <span
          aria-hidden
          className="absolute inset-[27%] rounded-full border bg-background/85 shadow-[0_10px_30px_-12px_rgb(49_46_129/0.6)] backdrop-blur-xl dark:bg-[#07070d]/85 dark:shadow-[0_14px_40px_-14px_rgb(0_0_0/0.9)]"
        />
        <svg viewBox="0 0 100 100" className="absolute inset-0 size-full overflow-visible" aria-hidden>
          <defs>
            <linearGradient id="sf-scroll-arc" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stopColor="#818cf8" />
              <stop offset="0.5" stopColor="#a78bfa" />
              <stop offset="1" stopColor="#22d3ee" />
            </linearGradient>
          </defs>
          {/* the track, and over it the arc the page has covered so far */}
          <circle cx="50" cy="50" r={ARC_R} fill="none" strokeWidth="3" className="stroke-foreground/12" />
          <motion.circle
            cx="50"
            cy="50"
            r={ARC_R}
            fill="none"
            stroke="url(#sf-scroll-arc)"
            strokeWidth="3"
            strokeLinecap="round"
            transform="rotate(-90 50 50)"
            style={{ pathLength: scrollYProgress }}
          />
          <motion.g
            data-testid="scroll-ring"
            className="fill-foreground/70 transition group-hover:fill-foreground"
            style={{ rotate: spin, originX: "50px", originY: "50px" }}
          >
            {ring.map(({ char, angle }, i) => (
              <text
                key={i}
                x="50"
                y={50 - NAME_R}
                textAnchor="middle"
                dominantBaseline="central"
                transform={`rotate(${angle.toFixed(2)} 50 50)`}
                style={{ fontSize: "8.6px", fontWeight: 900 }}
              >
                {char}
              </text>
            ))}
          </motion.g>
        </svg>
        {/* the number is the only thing in flow, so it sits dead centre; the
            hint floats under it rather than pushing it off the middle */}
        <span
          aria-hidden
          className={cn(
            "relative text-[13px] leading-none font-black tabular-nums sm:text-[17px]",
            atTop
              ? "text-muted-foreground"
              : "bg-linear-to-l from-indigo-500 via-violet-500 to-cyan-500 bg-clip-text text-transparent dark:from-indigo-300 dark:via-violet-300 dark:to-cyan-300",
          )}
        >
          {faNum(pct)}٪
        </span>
        {!atTop && (
          <ArrowUp
            aria-hidden
            className="absolute bottom-[26%] size-3 text-muted-foreground opacity-0 transition group-hover:opacity-100 group-focus-visible:opacity-100"
          />
        )}
      </button>
    </div>
  );
}
