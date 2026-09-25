"use client";

// Three logo concepts in the «شب نیلی» look, drawn as isometric solids with one
// light coming from the top left: the face turned toward it is the lightest,
// the face turned away the darkest, and a thin white edge catches the light.
// Everything is plain SVG with presentation attributes (no <style>, so the
// strict CSP is happy) and every gradient id is made unique per instance, so
// ten marks on one page never borrow each other's paint.
//
// The owner picks one concept later; the rest of the app never imports this
// file directly but goes through `logo.tsx`, the one switch point.

import { useReducedMotion } from "motion/react";
import { useId, useSyncExternalStore } from "react";
import { cn } from "cn";

/* ───────────────────────── geometry helpers ───────────────────────── */

type Pt = readonly [number, number];
const COS30 = Math.cos(Math.PI / 6);
const r2 = (n: number) => Math.round(n * 100) / 100;

/** Isometric projection: x runs to the lower right, y to the lower left, z up. */
function projector(ox: number, oy: number, s: number) {
  return (x: number, y: number, z: number): Pt => [ox + (x - y) * COS30 * s, oy + (x + y) * 0.5 * s - z * s];
}

const poly = (ps: Pt[]) => `M${ps.map(([x, y]) => `${r2(x)} ${r2(y)}`).join("L")}Z`;
const line = (ps: Pt[]) => `M${ps.map(([x, y]) => `${r2(x)} ${r2(y)}`).join("L")}`;

/** A gradient id that is safe inside url(#…) whatever React's useId returns. */
function useGradientIds<const K extends string>(keys: readonly K[]): Record<K, string> {
  const base = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  return Object.fromEntries(keys.map((k) => [k, `sf${base}${k}`])) as Record<K, string>;
}

const noop = () => () => {};

/** Animation runs only when asked for and never under reduced motion. It
 *  starts after hydration: the server cannot know the motion preference, so
 *  the first client render must match its still markup. */
function useAnimate(animated: boolean | undefined) {
  const reduce = useReducedMotion();
  const hydrated = useSyncExternalStore(noop, () => true, () => false);
  return !!animated && hydrated && !reduce;
}

export type MarkProps = {
  /** Rendered width and height in pixels (the art is square). */
  size?: number;
  /** A gentle loop (SMIL): off by default and always off under reduced motion. */
  animated?: boolean;
  /** Accessible name. Without it the mark is decorative (aria-hidden). */
  title?: string;
  className?: string;
};

function Svg({ size = 32, title, className, children }: MarkProps & { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 64 64"
      width={size}
      height={size}
      className={cn("shrink-0 overflow-visible", className)}
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      focusable="false"
    >
      {children}
    </svg>
  );
}

/* ═════════════════ (الف) the house whose wall carries an S ═════════════════ */
// A gabled house seen from above-left. The long wall facing the light carries
// a raised S (the flow through the property), the gable end sits in shadow.

const A = (() => {
  const L = 26, D = 18, H = 18, R = 7.5;
  const p = projector(27.4, 31.2, 1.3);
  const ridge0 = p(0, D / 2, H + R), ridge1 = p(L, D / 2, H + R);
  // The S, drawn on the wall's own plane (u along the wall, v up), then
  // projected onto it. `lift` raises it off the wall toward the viewer.
  const uL = 4.2, uR = 21.8, vt = 14.6, vb = 3.4, vm = (vt + vb) / 2, r = (vt - vm) / 2;
  const s2d: Pt[] = [[uR, vt], [uL + r, vt]];
  for (let k = 1; k <= 8; k++) {
    const a = Math.PI / 2 + (Math.PI * k) / 8;
    s2d.push([uL + r + Math.cos(a) * r, vm + r + Math.sin(a) * r]);
  }
  s2d.push([uR - r, vm]);
  for (let k = 1; k <= 8; k++) {
    const a = Math.PI / 2 - (Math.PI * k) / 8;
    s2d.push([uR - r + Math.cos(a) * r, vb + r + Math.sin(a) * r]);
  }
  s2d.push([uL, vb]);
  const sPath = (lift: number) => line(s2d.map(([u, v]) => p(u, D + lift, v)));
  return {
    roofFar: poly([ridge0, ridge1, p(L, 0, H), p(0, 0, H)]),
    roofNear: poly([p(0, D, H), p(L, D, H), ridge1, ridge0]),
    wall: poly([p(0, D, 0), p(L, D, 0), p(L, D, H), p(0, D, H)]),
    gable: poly([p(L, D, 0), p(L, 0, 0), p(L, 0, H), ridge1, p(L, D, H)]),
    ridge: line([ridge0, ridge1]),
    eave: line([p(0, D, H), p(L, D, H)]),
    corner: line([p(L, D, 0), p(L, D, H)]),
    sBase: sPath(0),
    sTop: sPath(1.5),
    sLength: 64,
  };
})();

export function HouseMark(props: MarkProps) {
  const id = useGradientIds(["roof", "wall", "gable", "s", "far"] as const);
  const play = useAnimate(props.animated);
  return (
    <Svg {...props}>
      <defs>
        <linearGradient id={id.roof} x1="10" y1="4" x2="40" y2="30" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#c7d2fe" />
          <stop offset="1" stopColor="#818cf8" />
        </linearGradient>
        <linearGradient id={id.far} x1="30" y1="4" x2="56" y2="22" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#818cf8" />
          <stop offset="1" stopColor="#6366f1" />
        </linearGradient>
        <linearGradient id={id.wall} x1="4" y1="24" x2="40" y2="60" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#6366f1" />
          <stop offset="1" stopColor="#7c3aed" />
        </linearGradient>
        <linearGradient id={id.gable} x1="40" y1="14" x2="60" y2="50" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#4338ca" />
          <stop offset="1" stopColor="#3b0764" />
        </linearGradient>
        <linearGradient id={id.s} x1="8" y1="26" x2="40" y2="56" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#a5f3fc" />
          <stop offset="0.5" stopColor="#22d3ee" />
          <stop offset="1" stopColor="#06b6d4" />
        </linearGradient>
      </defs>
      <path d={A.roofFar} fill={`url(#${id.far})`} />
      <path d={A.gable} fill={`url(#${id.gable})`} />
      <path d={A.wall} fill={`url(#${id.wall})`} />
      <path d={A.roofNear} fill={`url(#${id.roof})`} />
      <path d={A.sBase} fill="none" stroke="#0e7490" strokeWidth={4.4} strokeLinecap="round" strokeLinejoin="round" />
      <path d={A.sTop} fill="none" stroke={`url(#${id.s})`} strokeWidth={4.4} strokeLinecap="round" strokeLinejoin="round" />
      {play && (
        <path
          d={A.sTop}
          fill="none"
          stroke="#ffffff"
          strokeOpacity={0.85}
          strokeWidth={2}
          strokeLinecap="round"
          strokeDasharray={`6 ${A.sLength * 2}`}
        >
          <animate attributeName="stroke-dashoffset" values={`${A.sLength + 8};-${A.sLength}`} dur="2.8s" repeatCount="indefinite" />
        </path>
      )}
      <path d={A.ridge} stroke="#ffffff" strokeOpacity={0.8} strokeWidth={1} strokeLinecap="round" />
      <path d={A.eave} stroke="#ffffff" strokeOpacity={0.45} strokeWidth={0.8} strokeLinecap="round" />
      <path d={A.corner} stroke="#ffffff" strokeOpacity={0.3} strokeWidth={0.8} strokeLinecap="round" />
    </Svg>
  );
}

/* ═════════════════ (ب) the folded ribbon: a roof that turns into an S ═════════════════ */
// One strip of ribbon folded four times: its first two runs are a roof, the
// next two finish the S. Runs that slope like "/" face the light, runs that
// slope like "\" show the ribbon's back, in shadow. Folds are vertical, the
// way an isometric strip folds.

const B = (() => {
  const w = 8.5, t = 2.2; // ribbon width and thickness
  const P: Pt[] = [[51, 18.5], [32, 6.5], [13, 18.5], [51, 35], [13, 49]];
  const seg = (a: Pt, b: Pt) => poly([a, b, [b[0], b[1] + w], [a[0], a[1] + w]]);
  const edge = (a: Pt, b: Pt) => poly([[a[0], a[1] + w], [b[0], b[1] + w], [b[0], b[1] + w + t], [a[0], a[1] + w + t]]);
  const top = (a: Pt, b: Pt) => line([a, b]);
  return {
    segs: [0, 1, 2, 3].map((i) => ({ face: seg(P[i], P[i + 1]), edge: edge(P[i], P[i + 1]), top: top(P[i], P[i + 1]) })),
    spine: line(P.map(([x, y]) => [x, y + w / 2] as Pt)),
  };
})();

export function RibbonMark(props: MarkProps) {
  const id = useGradientIds(["l1", "l2", "d1", "d2", "sheen"] as const);
  const play = useAnimate(props.animated);
  const [s1, s2, s3, s4] = B.segs;
  return (
    <Svg {...props}>
      <defs>
        <linearGradient id={id.d1} x1="51" y1="18" x2="32" y2="10" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#312e81" />
          <stop offset="1" stopColor="#4f46e5" />
        </linearGradient>
        <linearGradient id={id.l1} x1="13" y1="20" x2="32" y2="8" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#818cf8" />
          <stop offset="1" stopColor="#c7d2fe" />
        </linearGradient>
        <linearGradient id={id.d2} x1="13" y1="22" x2="51" y2="40" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#2e1065" />
          <stop offset="1" stopColor="#6d28d9" />
        </linearGradient>
        <linearGradient id={id.l2} x1="51" y1="36" x2="13" y2="54" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#a78bfa" />
          <stop offset="0.55" stopColor="#8b5cf6" />
          <stop offset="1" stopColor="#22d3ee" />
        </linearGradient>
        {play && (
        <linearGradient id={id.sheen} x1="0" y1="0" x2="64" y2="64" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#ffffff" stopOpacity={0} />
          <stop offset="0.45" stopColor="#ffffff" stopOpacity={0}>
            <animate attributeName="offset" values="-0.2;1" dur="3.2s" repeatCount="indefinite" />
          </stop>
          <stop offset="0.5" stopColor="#ffffff" stopOpacity={0.55}>
            <animate attributeName="offset" values="-0.1;1.1" dur="3.2s" repeatCount="indefinite" />
          </stop>
          <stop offset="0.55" stopColor="#ffffff" stopOpacity={0}>
            <animate attributeName="offset" values="0;1.2" dur="3.2s" repeatCount="indefinite" />
          </stop>
        </linearGradient>
        )}
      </defs>
      {/* back side runs first, the lit runs fold over them */}
      <path d={s1.edge} fill="#1e1b4b" />
      <path d={s1.face} fill={`url(#${id.d1})`} />
      <path d={s3.edge} fill="#1e1b4b" />
      <path d={s3.face} fill={`url(#${id.d2})`} />
      <path d={s2.edge} fill="#3730a3" />
      <path d={s2.face} fill={`url(#${id.l1})`} />
      <path d={s4.edge} fill="#155e75" />
      <path d={s4.face} fill={`url(#${id.l2})`} />
      {play && (
        <g fill={`url(#${id.sheen})`}>
          <path d={s2.face} />
          <path d={s4.face} />
        </g>
      )}
      <path d={s2.top} stroke="#ffffff" strokeOpacity={0.85} strokeWidth={1} strokeLinecap="round" />
      <path d={s4.top} stroke="#ffffff" strokeOpacity={0.55} strokeWidth={0.8} strokeLinecap="round" />
      <path d={s1.top} stroke="#a5b4fc" strokeOpacity={0.5} strokeWidth={0.8} strokeLinecap="round" />
    </Svg>
  );
}

/* ═════════════════ (ج) three rising towers and an orbit ═════════════════ */
// Three isometric towers, each taller than the one before, with a ring
// around them: the office's listings growing, and the data circling them.

const C = (() => {
  const b = 9, g = 2.6;
  const p = projector(22.4, 48.6, 1);
  const heights = [14, 22.5, 31.5];
  const towers = heights.map((h, i) => {
    const y0 = -i * (b + g);
    return {
      top: poly([p(0, y0, h), p(b, y0, h), p(b, y0 + b, h), p(0, y0 + b, h)]),
      left: poly([p(0, y0 + b, 0), p(b, y0 + b, 0), p(b, y0 + b, h), p(0, y0 + b, h)]),
      right: poly([p(b, y0, 0), p(b, y0 + b, 0), p(b, y0 + b, h), p(b, y0, h)]),
      edge: line([p(0, y0 + b, h), p(b, y0 + b, h), p(b, y0, h)]),
      // lit windows on the face toward the light, one per storey band
      windows: Array.from({ length: Math.floor(h / 6) }, (_, k) =>
        line([p(2.2, y0 + b, 4 + k * 6), p(b - 2.2, y0 + b, 4 + k * 6)]),
      ),
    };
  });
  // The ring: an ellipse, tilted, split into its back and front halves so the
  // towers stand inside it.
  const cx = 32, cy = 35, rx = 29, ry = 8.2, tilt = -16;
  const at = (a: number): Pt => {
    const tr = (tilt * Math.PI) / 180;
    const x = Math.cos(a) * rx, y = Math.sin(a) * ry;
    return [cx + x * Math.cos(tr) - y * Math.sin(tr), cy + x * Math.sin(tr) + y * Math.cos(tr)];
  };
  const arc = (a0: number, a1: number) => {
    const [x0, y0] = at(a0), [x1, y1] = at(a1);
    return `M${r2(x0)} ${r2(y0)}A${rx} ${ry} ${tilt} 0 1 ${r2(x1)} ${r2(y1)}`;
  };
  const [sx, sy] = at(Math.PI * 0.62);
  return {
    towers: towers.reverse(), // painter's order: the tall one at the back first
    back: arc(Math.PI, Math.PI * 2),
    front: arc(0, Math.PI),
    orbit: `${arc(0, Math.PI)}${arc(Math.PI, Math.PI * 2).replace(/^M[^A]+/, "")}`,
    sat: [r2(sx), r2(sy)] as Pt,
  };
})();

export function TowersMark(props: MarkProps) {
  const id = useGradientIds(["top", "left", "right", "ring", "glow"] as const);
  const play = useAnimate(props.animated);
  return (
    <Svg {...props}>
      <defs>
        <linearGradient id={id.top} x1="10" y1="6" x2="44" y2="40" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#e0e7ff" />
          <stop offset="1" stopColor="#a5b4fc" />
        </linearGradient>
        <linearGradient id={id.left} x1="10" y1="14" x2="30" y2="62" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#818cf8" />
          <stop offset="1" stopColor="#7c3aed" />
        </linearGradient>
        <linearGradient id={id.right} x1="30" y1="10" x2="56" y2="60" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#4338ca" />
          <stop offset="1" stopColor="#2e1065" />
        </linearGradient>
        <linearGradient id={id.ring} x1="3" y1="30" x2="61" y2="40" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#22d3ee" />
          <stop offset="0.5" stopColor="#a5b4fc" />
          <stop offset="1" stopColor="#8b5cf6" />
        </linearGradient>
        <radialGradient id={id.glow}>
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="0.4" stopColor="#67e8f9" />
          <stop offset="1" stopColor="#22d3ee" stopOpacity={0} />
        </radialGradient>
      </defs>
      <path d={C.back} fill="none" stroke={`url(#${id.ring})`} strokeOpacity={0.55} strokeWidth={2.2} strokeLinecap="round" />
      {C.towers.map((t, i) => (
        <g key={i}>
          <path d={t.right} fill={`url(#${id.right})`} />
          <path d={t.left} fill={`url(#${id.left})`} />
          <path d={t.top} fill={`url(#${id.top})`} />
          {t.windows.map((w, k) => (
            <path key={k} d={w} stroke="#a5f3fc" strokeOpacity={0.7} strokeWidth={0.9} strokeLinecap="round" />
          ))}
          <path d={t.edge} fill="none" stroke="#ffffff" strokeOpacity={0.75} strokeWidth={0.8} strokeLinejoin="round" />
        </g>
      ))}
      <path d={C.front} fill="none" stroke={`url(#${id.ring})`} strokeWidth={2.6} strokeLinecap="round" />
      {play ? (
        <circle r={3.4} fill={`url(#${id.glow})`}>
          <animateMotion dur="5s" repeatCount="indefinite" path={C.orbit} />
        </circle>
      ) : (
        <circle cx={C.sat[0]} cy={C.sat[1]} r={3.4} fill={`url(#${id.glow})`} />
      )}
    </Svg>
  );
}

/* ───────────────────────── lockups ───────────────────────── */

export type LockupProps = MarkProps & {
  /** The brand as settings spell it; never hard-coded. */
  name: string;
  /** An optional second line (the tagline from settings). */
  tagline?: string;
  /** Classes for the name line (size, colour). */
  nameClassName?: string;
  /** Classes for the tagline (colour on a fixed background). */
  taglineClassName?: string;
};

function LockupFrame({
  Mark, name, tagline, nameClassName, taglineClassName, className, size = 36, ...mark
}: LockupProps & { Mark: (p: MarkProps) => React.ReactNode }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <Mark size={size} {...mark} className="drop-shadow-[0_6px_14px_rgb(99_102_241/0.35)]" />
      <span className="flex min-w-0 flex-col leading-tight">
        <span className={cn("truncate text-[15px] font-extrabold tracking-tight", nameClassName)}>{name}</span>
        {tagline && <span className={cn("truncate text-[11px] text-muted-foreground", taglineClassName)}>{tagline}</span>}
      </span>
    </span>
  );
}

export const HouseLockup = (p: LockupProps) => <LockupFrame Mark={HouseMark} {...p} />;
export const RibbonLockup = (p: LockupProps) => <LockupFrame Mark={RibbonMark} {...p} />;
export const TowersLockup = (p: LockupProps) => <LockupFrame Mark={TowersMark} {...p} />;

/** Every concept, for the gallery and for the switch in logo.tsx. */
export const CONCEPTS = {
  house: { Mark: HouseMark, Lockup: HouseLockup },
  ribbon: { Mark: RibbonMark, Lockup: RibbonLockup },
  towers: { Mark: TowersMark, Lockup: TowersLockup },
} as const;
export type ConceptKey = keyof typeof CONCEPTS;
