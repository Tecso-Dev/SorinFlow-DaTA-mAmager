"use client";

// A small isometric server rack for the monitoring header: a few units, a
// slow pulse on the status LED so the page reads as "watching something
// live" even before any data has loaded. CSS/SVG only, respects
// prefers-reduced-motion through MotionConfig at the app root.

import { motion, useReducedMotion } from "motion/react";
import { cn } from "cn";

const COS30 = Math.cos(Math.PI / 6);
const SIN30 = Math.sin(Math.PI / 6);
const iso = (x: number, y: number, z: number, s: number): [number, number] => [(x - y) * COS30 * s, (x + y) * SIN30 * s - z * s];
const pts = (list: [number, number][]) => list.map(([a, b]) => `${a.toFixed(1)},${b.toFixed(1)}`).join(" ");

export function IsoServerRack({ ok = true, className }: { ok?: boolean; className?: string }) {
  const reduce = useReducedMotion();
  const s = 14;
  const units = [0, 1, 2, 3];
  const w = 2.6;
  const d = 1.1;
  const uh = 0.42;
  const gap = 0.06;

  const front: [number, number][] = [iso(0, 0, 0, s), iso(w, 0, 0, s), iso(w, 0, uh, s), iso(0, 0, uh, s)];
  const side: [number, number][] = [iso(w, 0, 0, s), iso(w, d, 0, s), iso(w, d, uh, s), iso(w, 0, uh, s)];
  const top: [number, number][] = [iso(0, 0, uh, s), iso(w, 0, uh, s), iso(w, d, uh, s), iso(0, d, uh, s)];

  const allX = [...front, ...side, ...top].map((p) => p[0]);
  const topY = iso(0, 0, uh * units.length + 0.3, s)[1] - 6;
  const botY = iso(w, d, 0, s)[1] + 6;
  const vb = { x: Math.min(...allX) - 4, y: topY, w: Math.max(...allX) - Math.min(...allX) + 8, h: botY - topY };

  return (
    <div className={cn("relative", className)} aria-hidden>
      <svg viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`} className="h-full w-full">
        {units.map((u) => {
          const z = u * (uh + gap);
          return (
            <g key={u} transform={`translate(0 ${-z * s})`}>
              <polygon points={pts(side)} className="fill-muted stroke-border" strokeWidth={0.5} style={{ filter: "brightness(0.75)" }} />
              <polygon points={pts(top)} className="fill-card stroke-border" strokeWidth={0.5} />
              <polygon points={pts(front)} className="fill-card stroke-border" strokeWidth={0.5} />
              {/* rack ears / vents */}
              {[0.35, 0.7, 1.05, 1.4, 1.75, 2.1, 2.45].map((x) => {
                const a = iso(x, 0, uh * 0.72, s);
                const b = iso(x, 0, uh * 0.28, s);
                return <line key={x} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} className="stroke-border" strokeWidth={0.35} />;
              })}
              {u === units.length - 1 && (
                <motion.circle
                  cx={iso(w - 0.22, 0, uh * 0.5, s)[0]}
                  cy={iso(w - 0.22, 0, uh * 0.5, s)[1]}
                  r={0.16 * s}
                  className={ok ? "fill-success" : "fill-destructive"}
                  animate={reduce ? undefined : { opacity: [1, 0.35, 1] }}
                  transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
                />
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
