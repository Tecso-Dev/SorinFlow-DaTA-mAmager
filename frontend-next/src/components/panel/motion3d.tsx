"use client";

// The 3D pieces for the panel's weaker sections (divar, sms, email, ai,
// audit, profile, scraper, admin-portal) — the same two techniques already
// used by IsoBadge/Skyline/Donut3D/Shield3D elsewhere in the app: an
// isometric SVG built from stacked, offset copies for a cheap extrusion, or
// a real CSS 3D transform (`preserve-3d`, `rotateX/Y`, `translateZ`) with a
// `motion` spring. CSS/SVG only — no WebGL in the panel (that is the landing
// page's budget), so this stays smooth on a mid-range phone and behaves
// under `prefers-reduced-motion` (MotionConfig reducedMotion="user" already
// disables whileHover/animate loops app-wide; the few raw `useAnimationFrame`
// spins here check `useReducedMotion()` themselves, the same way Donut3D does).

import { motion, useAnimationFrame, useMotionValue, useReducedMotion, useSpring, useTransform } from "motion/react";
import { useRef, useState } from "react";
import { cn } from "cn";

const FLOAT = { duration: 5, repeat: Infinity, ease: "easeInOut" } as const;

/* ───────────────────────── isometric phone (divar login, sms) ───────────────────────── */

/** An extruded phone, floating — the login/SMS motif for divar and sms. */
export function IsoPhone({ className, tone = "indigo" }: { className?: string; tone?: "indigo" | "cyan" }) {
  const reduce = useReducedMotion();
  const screenTop = tone === "cyan" ? "#22d3ee" : "#a5b4fc";
  return (
    <motion.svg
      viewBox="0 0 160 200"
      className={cn("mx-auto w-full max-w-[140px] overflow-visible", className)}
      aria-hidden
      animate={reduce ? undefined : { y: [0, -7, 0] }}
      transition={FLOAT}
    >
      <defs>
        <linearGradient id="iso-phone-face" x1="20" y1="10" x2="140" y2="190" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor={screenTop} />
          <stop offset="1" stopColor="#4f46e5" />
        </linearGradient>
        <radialGradient id="iso-phone-shadow">
          <stop offset="0" stopColor="#6366f1" stopOpacity={0.4} />
          <stop offset="1" stopColor="#6366f1" stopOpacity={0} />
        </radialGradient>
      </defs>
      <ellipse cx="80" cy="192" rx="46" ry="8" fill="url(#iso-phone-shadow)" />
      {[9, 7, 5, 3, 1].map((k) => (
        <rect key={k} x={20 + k * 0.8} y={10 + k} width="120" height="170" rx="18" fill="#2e1065" opacity={0.3 + (9 - k) * 0.06} />
      ))}
      <rect x="20" y="10" width="120" height="170" rx="18" fill="url(#iso-phone-face)" stroke="#c7d2fe" strokeOpacity={0.5} />
      <rect x="32" y="30" width="96" height="120" rx="8" fill="#1e1b4b" opacity={0.85} />
      <rect x="60" y="18" width="40" height="5" rx="2.5" fill="#ffffff" opacity={0.5} />
      {[0, 1, 2].map((i) => (
        <circle key={i} cx={60 + i * 20} cy="90" r="5" fill={i === 1 ? "#22d3ee" : "#c7d2fe"} opacity={i === 1 ? 1 : 0.6} />
      ))}
      <rect x="50" y="160" width="60" height="6" rx="3" fill="#ffffff" opacity={0.35} />
    </motion.svg>
  );
}

/* ───────────────────────── isometric envelope (email) ───────────────────────── */

export function IsoEnvelope({ className }: { className?: string }) {
  const reduce = useReducedMotion();
  return (
    <motion.svg
      viewBox="0 0 200 160"
      className={cn("mx-auto w-full max-w-[180px] overflow-visible", className)}
      aria-hidden
      animate={reduce ? undefined : { y: [0, -6, 0] }}
      transition={FLOAT}
    >
      <defs>
        <linearGradient id="iso-env-face" x1="20" y1="20" x2="180" y2="140" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#eef2ff" />
          <stop offset="1" stopColor="#c7d2fe" />
        </linearGradient>
      </defs>
      <ellipse cx="100" cy="148" rx="70" ry="8" fill="#6366f1" opacity={0.22} />
      {[7, 5, 3, 1].map((k) => (
        <rect key={k} x={20} y={20 + k} width="160" height="104" rx="10" fill="#3730a3" opacity={0.25 + (7 - k) * 0.08} />
      ))}
      <rect x="20" y="20" width="160" height="104" rx="10" fill="url(#iso-env-face)" stroke="#4338ca" strokeOpacity={0.5} />
      <path d="M20 30 L100 84 L180 30" fill="none" stroke="#4338ca" strokeWidth={4} strokeLinejoin="round" strokeLinecap="round" />
      <motion.g animate={reduce ? undefined : { y: [0, -10, 0], opacity: [0.9, 1, 0.9] }} transition={{ duration: 3.4, repeat: Infinity, ease: "easeInOut" }}>
        <rect x="72" y="50" width="56" height="38" rx="4" fill="#ffffff" stroke="#22d3ee" strokeWidth={3} />
        <line x1="80" y1="61" x2="120" y2="61" stroke="#a5b4fc" strokeWidth={3} strokeLinecap="round" />
        <line x1="80" y1="72" x2="108" y2="72" stroke="#a5b4fc" strokeWidth={3} strokeLinecap="round" />
      </motion.g>
    </motion.svg>
  );
}

/* ───────────────────────── isometric AI chip (ai) ───────────────────────── */

export function IsoChip({ className }: { className?: string }) {
  const reduce = useReducedMotion();
  const pins = Array.from({ length: 5 }, (_, i) => 30 + i * 20);
  return (
    <motion.svg
      viewBox="0 0 160 160"
      className={cn("mx-auto w-full max-w-[140px] overflow-visible", className)}
      aria-hidden
      animate={reduce ? undefined : { y: [0, -6, 0] }}
      transition={FLOAT}
    >
      <defs>
        <linearGradient id="iso-chip-face" x1="20" y1="20" x2="140" y2="140" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#a5b4fc" />
          <stop offset="1" stopColor="#7c3aed" />
        </linearGradient>
      </defs>
      {pins.map((p) => (
        <g key={p}>
          <line x1={p} y1="8" x2={p} y2="26" stroke="#818cf8" strokeWidth={4} strokeLinecap="round" />
          <line x1={p} y1="134" x2={p} y2="152" stroke="#818cf8" strokeWidth={4} strokeLinecap="round" />
        </g>
      ))}
      {[9, 7, 5, 3, 1].map((k) => (
        <rect key={k} x={20 + k * 0.6} y={20 + k} width="120" height="120" rx="16" fill="#2e1065" opacity={0.28 + (9 - k) * 0.06} />
      ))}
      <rect x="20" y="20" width="120" height="120" rx="16" fill="url(#iso-chip-face)" stroke="#e0e7ff" strokeOpacity={0.5} />
      <motion.circle
        cx="80" cy="80" r="22" fill="#0e1030"
        animate={reduce ? undefined : { r: [20, 24, 20] }}
        transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
      />
      <motion.circle
        cx="80" cy="80" r="10" fill="#22d3ee"
        animate={reduce ? undefined : { opacity: [0.6, 1, 0.6], scale: [0.9, 1.15, 0.9] }}
        transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
      />
      {[[-24, -24], [24, -24], [-24, 24], [24, 24]].map(([dx, dy], i) => (
        <line key={i} x1="80" y1="80" x2={80 + dx} y2={80 + dy} stroke="#ecfeff" strokeWidth={2} strokeOpacity={0.5} />
      ))}
    </motion.svg>
  );
}

/* ───────────────────────── isometric shield (audit, security) ───────────────────────── */

export function IsoShield({ className, size = 140 }: { className?: string; size?: number }) {
  const reduce = useReducedMotion();
  const face = "M80 14 L136 36 V84 C136 118 112 140 80 152 C48 140 24 118 24 84 V36 Z";
  return (
    <motion.svg
      viewBox="0 0 160 170"
      width={size}
      className={cn("mx-auto overflow-visible", className)}
      style={{ maxWidth: "100%" }}
      aria-hidden
      animate={reduce ? undefined : { y: [0, -6, 0] }}
      transition={FLOAT}
    >
      <defs>
        <linearGradient id="iso-shield-face" x1="24" y1="14" x2="136" y2="152" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#a5b4fc" />
          <stop offset="0.55" stopColor="#6366f1" />
          <stop offset="1" stopColor="#7c3aed" />
        </linearGradient>
      </defs>
      <ellipse cx="80" cy="160" rx="48" ry="7" fill="#6366f1" opacity={0.3} />
      {[8, 6, 4, 2].map((k) => (
        <path key={k} d={face} transform={`translate(${k * 0.6} ${k})`} fill="#2e1065" opacity={0.3 + (8 - k) * 0.06} />
      ))}
      <path d={face} fill="url(#iso-shield-face)" stroke="#ffffff" strokeOpacity={0.5} strokeWidth={1.5} />
      <motion.circle
        cx="80" cy="82" r="26" fill="#22d3ee"
        animate={reduce ? undefined : { opacity: [0.18, 0.4, 0.18], scale: [1, 1.14, 1] }}
        transition={{ duration: 2.6, repeat: Infinity, ease: "easeInOut" }}
      />
      <rect x="66" y="78" width="28" height="22" rx="4" fill="#ffffff" />
      <path d="M71 78 V68 a9 9 0 0 1 18 0 V78" fill="none" stroke="#ffffff" strokeWidth={5} />
      <circle cx="80" cy="88" r="3.4" fill="#3730a3" />
    </motion.svg>
  );
}

/* ───────────────────────── isometric ID card (profile) ───────────────────────── */

export function IsoIDCard({ className }: { className?: string }) {
  const reduce = useReducedMotion();
  return (
    <motion.svg
      viewBox="0 0 180 130"
      className={cn("mx-auto w-full max-w-[170px] overflow-visible", className)}
      aria-hidden
      animate={reduce ? undefined : { y: [0, -6, 0], rotate: [-2, 2, -2] }}
      transition={{ duration: 6, repeat: Infinity, ease: "easeInOut" }}
    >
      <defs>
        <linearGradient id="iso-id-face" x1="10" y1="10" x2="170" y2="120" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#818cf8" />
          <stop offset="1" stopColor="#6d28d9" />
        </linearGradient>
      </defs>
      {[7, 5, 3, 1].map((k) => (
        <rect key={k} x={10 + k * 0.7} y={10 + k} width="160" height="100" rx="14" fill="#2e1065" opacity={0.28 + (7 - k) * 0.07} />
      ))}
      <rect x="10" y="10" width="160" height="100" rx="14" fill="url(#iso-id-face)" stroke="#e0e7ff" strokeOpacity={0.5} />
      <circle cx="46" cy="52" r="20" fill="#eef2ff" opacity={0.92} />
      <circle cx="46" cy="46" r="8" fill="#6d28d9" opacity={0.6} />
      <path d="M30 66 a16 12 0 0 1 32 0 Z" fill="#6d28d9" opacity={0.6} />
      <rect x="80" y="38" width="72" height="7" rx="3.5" fill="#ffffff" opacity={0.85} />
      <rect x="80" y="54" width="52" height="6" rx="3" fill="#ffffff" opacity={0.5} />
      <rect x="80" y="68" width="60" height="6" rx="3" fill="#ffffff" opacity={0.5} />
      <rect x="24" y="88" width="132" height="10" rx="5" fill="#22d3ee" opacity={0.55} />
    </motion.svg>
  );
}

/* ───────────────────────── layered stack (scraper: pages → ads → leads) ───────────────────────── */

const ISO_COS = Math.cos(Math.PI / 6);
const ISO_SIN = Math.sin(Math.PI / 6);
function isoPt(x: number, y: number, z: number, s: number): [number, number] {
  return [(x - y) * ISO_COS * s, (x + y) * ISO_SIN * s - z * s];
}
function isoPts(list: [number, number][]) {
  return list.map(([a, b]) => `${a.toFixed(1)},${b.toFixed(1)}`).join(" ");
}

/** Three flat isometric platforms stacked with a gap — the scraper's funnel: pages, ads, leads. */
export function LayerStack({ labels, className }: { labels: [string, string, string]; className?: string }) {
  const reduce = useReducedMotion();
  const s = 30;
  const half = 2.1;
  const colors = ["#4338ca", "#6366f1", "#22d3ee"];
  const plane = (cx: number, cy: number): [number, number][] => [
    isoPt(cx - half, cy - half, 0, s), isoPt(cx + half, cy - half, 0, s), isoPt(cx + half, cy + half, 0, s), isoPt(cx - half, cy + half, 0, s),
  ];
  const layers = [0, 1, 2].map((i) => ({ i, pts: plane(0, 0), y: -i * 34, color: colors[i], label: labels[i] }));
  const xs = layers.flatMap((l) => l.pts.map((p) => p[0]));
  const vb = { x: Math.min(...xs) - 30, w: Math.max(...xs) - Math.min(...xs) + 60 };
  return (
    <svg viewBox={`${vb.x} -132 ${vb.w} 172`} className={cn("mx-auto w-full max-w-[220px] overflow-visible", className)} role="img" aria-label="روند اسکرپ: صفحات به آگهی به لید">
      {layers.map((l) => (
        <motion.g
          key={l.i}
          animate={reduce ? undefined : { y: [l.y, l.y - 5, l.y] }}
          transition={{ duration: 4.2, repeat: Infinity, ease: "easeInOut", delay: l.i * 0.3 }}
          initial={false}
          style={{ translateY: l.y }}
        >
          <polygon points={isoPts(l.pts)} fill={l.color} fillOpacity={0.85} stroke="#e0e7ff" strokeOpacity={0.4} />
          <text x="0" y="4" textAnchor="middle" className="fill-white text-[10px] font-bold">{l.label}</text>
        </motion.g>
      ))}
      {[0, 1].map((i) => (
        <line
          key={i}
          x1="0" y1={layers[i].y - 12} x2="0" y2={layers[i + 1].y + 12}
          stroke="#a5b4fc" strokeWidth={2} strokeDasharray="3 3" markerEnd="url(#arrow)"
        />
      ))}
      <defs>
        <marker id="arrow" markerWidth="8" markerHeight="8" refX="4" refY="4" orient="auto">
          <path d="M0 0 L8 4 L0 8 Z" fill="#a5b4fc" />
        </marker>
      </defs>
    </svg>
  );
}

/* ───────────────────────── 3D card deck (admin-portal requests) ───────────────────────── */

/** A fanned deck of request cards in real CSS 3D — `preserve-3d` + a spring
 *  that leans toward the pointer, the same recipe as `Tilt` in viz.tsx. */
export function CardDeck3D({ className }: { className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();
  const [hover, setHover] = useState(false);
  const px = useMotionValue(0.5);
  const spring = { stiffness: 200, damping: 20, mass: 0.6 };
  const rotateY = useSpring(useTransform(px, [0, 1], [-14, 14]), spring);
  const bob = useMotionValue(0);
  useAnimationFrame((t) => {
    if (!reduce) bob.set(Math.sin(t / 900) * 4);
  });

  function onMove(e: React.PointerEvent) {
    if (reduce || e.pointerType !== "mouse" || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    px.set((e.clientX - r.left) / r.width);
  }

  const cards = [
    { z: 0, r: -10, y: 10, tone: "#4338ca" },
    { z: 16, r: -2, y: 4, tone: "#6366f1" },
    { z: 32, r: 8, y: -4, tone: "#8b5cf6" },
  ];

  return (
    <div
      ref={ref}
      onPointerMove={onMove}
      onPointerEnter={() => setHover(true)}
      onPointerLeave={() => { setHover(false); px.set(0.5); }}
      className={cn("mx-auto grid h-[150px] w-full max-w-[190px] place-items-center", className)}
      style={{ perspective: 700 }}
      aria-hidden
    >
      <motion.div className="relative h-[104px] w-[150px]" style={{ transformStyle: "preserve-3d", rotateY, y: bob }}>
        {cards.map((c, i) => (
          <motion.div
            key={i}
            className="absolute inset-0 rounded-2xl border border-white/25 shadow-[0_14px_30px_-12px_rgb(79_70_229/0.55)]"
            style={{
              background: `linear-gradient(135deg, ${c.tone}, #1e1b4b)`,
              transform: `translateZ(${c.z}px) rotate(${c.r}deg) translateY(${c.y}px)`,
              transformStyle: "preserve-3d",
            }}
            animate={hover && !reduce ? { scale: 1.03 } : { scale: 1 }}
          >
            <div className="flex h-full flex-col justify-between p-3">
              <div className="h-2 w-2/3 rounded-full bg-white/60" />
              <div className="flex items-end justify-between">
                <div className="h-1.5 w-8 rounded-full bg-white/40" />
                <div className="size-4 rounded-full bg-cyan-300/80" />
              </div>
            </div>
          </motion.div>
        ))}
      </motion.div>
    </div>
  );
}
