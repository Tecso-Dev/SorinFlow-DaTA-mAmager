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
import { useId, useRef, useState } from "react";
import { cn } from "cn";

const FLOAT = { duration: 5, repeat: Infinity, ease: "easeInOut" } as const;

/** Gradient ids that belong to one instance.
 *
 *  An SVG id is global to the document, so two of the same piece on one page
 *  would both paint from whichever <linearGradient> the browser saw last —
 *  the mark that mounted second silently wearing the first one's colours, or
 *  nothing at all if it unmounts. Every piece here appears once today, which
 *  is exactly the kind of "true for now" that breaks the day a section is
 *  reused. components/brand/logos.tsx solved this the same way. */
function useSvgIds<const K extends string>(keys: readonly K[]): Record<K, string> {
  const base = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  return Object.fromEntries(keys.map((k) => [k, `m3${base}${k}`])) as Record<K, string>;
}

/* ───────────────────────── isometric phone (divar login, sms) ───────────────────────── */

/** An extruded phone, floating — the login/SMS motif for divar and sms. */
export function IsoPhone({ className, tone = "indigo" }: { className?: string; tone?: "indigo" | "cyan" }) {
  const gid = useSvgIds(["phone_face", "phone_shadow"] as const);
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
        <linearGradient id={gid.phone_face} x1="20" y1="10" x2="140" y2="190" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor={screenTop} />
          <stop offset="1" stopColor="#4f46e5" />
        </linearGradient>
        <radialGradient id={gid.phone_shadow}>
          <stop offset="0" stopColor="#6366f1" stopOpacity={0.4} />
          <stop offset="1" stopColor="#6366f1" stopOpacity={0} />
        </radialGradient>
      </defs>
      <ellipse cx="80" cy="192" rx="46" ry="8" fill={`url(#${gid.phone_shadow})`} />
      {[9, 7, 5, 3, 1].map((k) => (
        <rect key={k} x={20 + k * 0.8} y={10 + k} width="120" height="170" rx="18" fill="#2e1065" opacity={0.3 + (9 - k) * 0.06} />
      ))}
      <rect x="20" y="10" width="120" height="170" rx="18" fill={`url(#${gid.phone_face})`} stroke="#c7d2fe" strokeOpacity={0.5} />
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
  const gid = useSvgIds(["env_face"] as const);
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
        <linearGradient id={gid.env_face} x1="20" y1="20" x2="180" y2="140" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#eef2ff" />
          <stop offset="1" stopColor="#c7d2fe" />
        </linearGradient>
      </defs>
      <ellipse cx="100" cy="148" rx="70" ry="8" fill="#6366f1" opacity={0.22} />
      {[7, 5, 3, 1].map((k) => (
        <rect key={k} x={20} y={20 + k} width="160" height="104" rx="10" fill="#3730a3" opacity={0.25 + (7 - k) * 0.08} />
      ))}
      <rect x="20" y="20" width="160" height="104" rx="10" fill={`url(#${gid.env_face})`} stroke="#4338ca" strokeOpacity={0.5} />
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
  const gid = useSvgIds(["chip_face"] as const);
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
        <linearGradient id={gid.chip_face} x1="20" y1="20" x2="140" y2="140" gradientUnits="userSpaceOnUse">
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
      <rect x="20" y="20" width="120" height="120" rx="16" fill={`url(#${gid.chip_face})`} stroke="#e0e7ff" strokeOpacity={0.5} />
      {/* scale, not r: animating the attribute itself made motion write
          r="undefined" for a frame while it set the animation up, which the
          browser reports as an error and the e2e specs count as a page
          problem. A transform cannot be written as a non-length, and at this
          size the two look identical (22 × 0.91…1.09 = 20…24). */}
      <motion.circle
        cx="80" cy="80" r="22" fill="#0e1030"
        animate={reduce ? undefined : { scale: [0.91, 1.09, 0.91] }}
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
  const gid = useSvgIds(["shield_face"] as const);
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
        <linearGradient id={gid.shield_face} x1="24" y1="14" x2="136" y2="152" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#a5b4fc" />
          <stop offset="0.55" stopColor="#6366f1" />
          <stop offset="1" stopColor="#7c3aed" />
        </linearGradient>
      </defs>
      <ellipse cx="80" cy="160" rx="48" ry="7" fill="#6366f1" opacity={0.3} />
      {[8, 6, 4, 2].map((k) => (
        <path key={k} d={face} transform={`translate(${k * 0.6} ${k})`} fill="#2e1065" opacity={0.3 + (8 - k) * 0.06} />
      ))}
      <path d={face} fill={`url(#${gid.shield_face})`} stroke="#ffffff" strokeOpacity={0.5} strokeWidth={1.5} />
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
  const gid = useSvgIds(["id_face"] as const);
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
        <linearGradient id={gid.id_face} x1="10" y1="10" x2="170" y2="120" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#818cf8" />
          <stop offset="1" stopColor="#6d28d9" />
        </linearGradient>
      </defs>
      {[7, 5, 3, 1].map((k) => (
        <rect key={k} x={10 + k * 0.7} y={10 + k} width="160" height="100" rx="14" fill="#2e1065" opacity={0.28 + (7 - k) * 0.07} />
      ))}
      <rect x="10" y="10" width="160" height="100" rx="14" fill={`url(#${gid.id_face})`} stroke="#e0e7ff" strokeOpacity={0.5} />
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

/* ───────────────────────── isometric warning sign (error states) ───────────────────────── */

/** A floating extruded warning triangle: the picture every «could not load»
 *  card in the second design pass carries, so a failure looks like a
 *  deliberate state and not an empty box. */
export function IsoAlert({ className }: { className?: string }) {
  const gid = useSvgIds(["alert_face"] as const);
  const reduce = useReducedMotion();
  const tri = "M70 12 L126 108 Q132 120 118 120 H22 Q8 120 14 108 Z";
  return (
    <motion.svg
      viewBox="0 0 140 150"
      className={cn("mx-auto w-full max-w-[96px] overflow-visible", className)}
      aria-hidden
      animate={reduce ? undefined : { y: [0, -5, 0] }}
      transition={FLOAT}
    >
      <defs>
        <linearGradient id={gid.alert_face} x1="14" y1="12" x2="126" y2="120" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#fbbf24" />
          <stop offset="1" stopColor="#e11d48" />
        </linearGradient>
      </defs>
      <ellipse cx="70" cy="140" rx="40" ry="6" fill="#e11d48" opacity={0.25} />
      {[8, 6, 4, 2].map((k) => (
        <path key={k} d={tri} transform={`translate(${k * 0.6} ${k})`} fill="#4c0519" opacity={0.3 + (8 - k) * 0.06} />
      ))}
      <path d={tri} fill={`url(#${gid.alert_face})`} stroke="#ffffff" strokeOpacity={0.5} strokeWidth={1.5} />
      <rect x="64" y="46" width="12" height="40" rx="6" fill="#ffffff" />
      <motion.circle
        cx="70" cy="100" r="7" fill="#ffffff"
        animate={reduce ? undefined : { scale: [1, 1.25, 1] }}
        transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
      />
    </motion.svg>
  );
}

/* ───────────────────────── relay antenna (monitoring) ───────────────────────── */

/** A lattice mast on a rack-sized base, its beacon sending rings outward:
 *  «something is watching, and listening». The rings are circles scaled and
 *  faded by transform (never `r`), staggered so one is always on its way. */
export function IsoAntenna({ className }: { className?: string }) {
  const gid = useSvgIds(["ant_base", "ant_mast"] as const);
  const reduce = useReducedMotion();
  // the mast's legs meet at the beacon (90,34) and spread 24 px each side by y=152
  const leg = (y: number) => ((y - 34) * 24) / 118;
  const braces = [72, 98, 124];
  return (
    <motion.svg
      viewBox="0 0 180 200"
      className={cn("mx-auto w-full max-w-[150px] overflow-visible", className)}
      aria-hidden
      animate={reduce ? undefined : { y: [0, -4, 0] }}
      transition={FLOAT}
    >
      <defs>
        <linearGradient id={gid.ant_base} x1="40" y1="152" x2="140" y2="178" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#818cf8" />
          <stop offset="1" stopColor="#4f46e5" />
        </linearGradient>
        <linearGradient id={gid.ant_mast} x1="90" y1="34" x2="90" y2="152" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#e0e7ff" />
          <stop offset="1" stopColor="#6366f1" />
        </linearGradient>
      </defs>
      <ellipse cx="90" cy="190" rx="60" ry="7" fill="#6366f1" opacity={0.25} />
      {[8, 6, 4, 2].map((k) => (
        <rect key={k} x={40 + k * 0.5} y={152 + k} width="100" height="26" rx="10" fill="#2e1065" opacity={0.28 + (8 - k) * 0.06} />
      ))}
      <rect x="40" y="152" width="100" height="26" rx="10" fill={`url(#${gid.ant_base})`} stroke="#c7d2fe" strokeOpacity={0.5} />
      <rect x="54" y="162" width="26" height="5" rx="2.5" fill="#ffffff" opacity={0.45} />
      <rect x="86" y="162" width="14" height="5" rx="2.5" fill="#ffffff" opacity={0.25} />
      <motion.circle
        cx="122" cy="165" r="4" fill="#22d3ee"
        animate={reduce ? undefined : { opacity: [1, 0.35, 1] }}
        transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
      />
      {/* the mast, once dark behind and once lit in front, for the extrusion */}
      <path d="M93 36 L69 154 H117 Z" fill="none" stroke="#2e1065" strokeOpacity={0.5} strokeWidth={5} strokeLinejoin="round" />
      <path d="M90 34 L66 152 H114 Z" fill="none" stroke={`url(#${gid.ant_mast})`} strokeWidth={5} strokeLinejoin="round" />
      {braces.map((y, i) => {
        const next = braces[i + 1];
        return (
          <g key={y} stroke="#c7d2fe" strokeWidth={2.5} strokeLinecap="round" opacity={0.85}>
            <line x1={90 - leg(y)} y1={y} x2={90 + leg(y)} y2={y} />
            {next && <line x1={i % 2 ? 90 + leg(y) : 90 - leg(y)} y1={y} x2={i % 2 ? 90 - leg(next) : 90 + leg(next)} y2={next} />}
          </g>
        );
      })}
      {[0, 1, 2].map((i) => (
        <motion.circle
          key={i}
          cx="90" cy="30" r="12" fill="none" stroke="#22d3ee" strokeWidth={2} opacity={0.45}
          vectorEffect="non-scaling-stroke"
          animate={reduce ? undefined : { scale: [0.6, 2.7], opacity: [0.75, 0] }}
          transition={{ duration: 2.6, repeat: Infinity, ease: "easeOut", delay: i * 0.87 }}
        />
      ))}
      <circle cx="90" cy="30" r="10" fill="#0e1030" stroke="#c7d2fe" strokeOpacity={0.7} />
      <motion.circle
        cx="90" cy="30" r="5" fill="#22d3ee"
        animate={reduce ? undefined : { opacity: [0.65, 1, 0.65], scale: [0.9, 1.2, 0.9] }}
        transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
      />
    </motion.svg>
  );
}

/* ───────────────────────── router hops (proxies) ───────────────────────── */

/** Three router slabs climbing like steps, a dashed route between them and a
 *  packet hopping up the stairs: what a proxy chain is. The packet moves by
 *  `x`/`y` (transforms), never `cx`/`cy`. */
export function IsoHopStack({ className }: { className?: string }) {
  const gid = useSvgIds(["hop_face"] as const);
  const reduce = useReducedMotion();
  const slabs = [{ x: 14, y: 118 }, { x: 68, y: 76 }, { x: 122, y: 34 }];
  return (
    <motion.svg
      viewBox="0 0 200 170"
      className={cn("mx-auto w-full max-w-[170px] overflow-visible", className)}
      aria-hidden
      animate={reduce ? undefined : { y: [0, -4, 0] }}
      transition={FLOAT}
    >
      <defs>
        <linearGradient id={gid.hop_face} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#a5b4fc" />
          <stop offset="1" stopColor="#6d28d9" />
        </linearGradient>
      </defs>
      <ellipse cx="100" cy="160" rx="70" ry="7" fill="#6366f1" opacity={0.22} />
      {slabs.slice(0, 2).map((s, i) => (
        <line
          key={i}
          x1={s.x + 32} y1={s.y - 4} x2={slabs[i + 1].x + 32} y2={slabs[i + 1].y - 4}
          stroke="#a5b4fc" strokeWidth={2} strokeDasharray="4 4" strokeLinecap="round"
        />
      ))}
      {slabs.map((s, i) => (
        <g key={i}>
          {[6, 4, 2].map((k) => (
            <rect key={k} x={s.x + k * 0.6} y={s.y + k} width="64" height="24" rx="8" fill="#2e1065" opacity={0.3 + (6 - k) * 0.08} />
          ))}
          <rect x={s.x} y={s.y} width="64" height="24" rx="8" fill={`url(#${gid.hop_face})`} stroke="#e0e7ff" strokeOpacity={0.5} />
          <rect x={s.x + 10} y={s.y + 10} width="26" height="4" rx="2" fill="#ffffff" opacity={0.45} />
          {[0, 1, 2].map((d) => (
            <circle key={d} cx={s.x + 46 + d * 6} cy={s.y + 12} r="2" fill={d === i ? "#22d3ee" : "#c7d2fe"} opacity={d === i ? 1 : 0.55} />
          ))}
        </g>
      ))}
      <motion.circle
        cx={slabs[0].x + 32} cy={slabs[0].y - 4} r="5" fill="#22d3ee" opacity={0}
        animate={reduce ? undefined : { x: [0, 5, 54, 103, 108], y: [0, -4, -42, -80, -84], opacity: [0, 1, 1, 1, 0] }}
        transition={{ duration: 3.2, repeat: Infinity, ease: "linear", times: [0, 0.08, 0.5, 0.92, 1] }}
      />
    </motion.svg>
  );
}

/* ───────────────────────── key + sliders (settings) ───────────────────────── */

/** A settings panel with three sliders that keep moving, and a key resting on
 *  its corner. Each fill grows and shrinks by `scaleX` from its own left
 *  edge, and its knob travels the same distance by `x`, so they stay joined
 *  without animating a width. */
export function IsoKeyPanel({ className }: { className?: string }) {
  const gid = useSvgIds(["kp_face", "kp_key"] as const);
  const reduce = useReducedMotion();
  const tracks = [
    { y: 46, w: 62, d: 14, dur: 3.4 },
    { y: 68, w: 38, d: 12, dur: 4.1 },
    { y: 90, w: 74, d: 16, dur: 3.8 },
  ];
  return (
    <motion.svg
      viewBox="0 0 190 170"
      className={cn("mx-auto w-full max-w-[160px] overflow-visible", className)}
      aria-hidden
      animate={reduce ? undefined : { y: [0, -5, 0] }}
      transition={FLOAT}
    >
      <defs>
        <linearGradient id={gid.kp_face} x1="14" y1="14" x2="146" y2="118" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#818cf8" />
          <stop offset="1" stopColor="#6d28d9" />
        </linearGradient>
        <linearGradient id={gid.kp_key} x1="0" y1="-14" x2="60" y2="14" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#fde68a" />
          <stop offset="1" stopColor="#f59e0b" />
        </linearGradient>
      </defs>
      <ellipse cx="90" cy="160" rx="66" ry="7" fill="#6366f1" opacity={0.22} />
      {[8, 6, 4, 2].map((k) => (
        <rect key={k} x={14 + k * 0.7} y={14 + k} width="132" height="104" rx="16" fill="#2e1065" opacity={0.28 + (8 - k) * 0.06} />
      ))}
      <rect x="14" y="14" width="132" height="104" rx="16" fill={`url(#${gid.kp_face})`} stroke="#e0e7ff" strokeOpacity={0.5} />
      <rect x="26" y="26" width="108" height="80" rx="10" fill="#1e1b4b" opacity={0.85} />
      {tracks.map((t, i) => (
        <g key={i}>
          <rect x="38" y={t.y} width="84" height="6" rx="3" fill="#ffffff" opacity={0.22} />
          <motion.rect
            x="38" y={t.y} width={t.w} height="6" rx="3" fill="#22d3ee"
            style={{ originX: 0, originY: 0.5 }}
            animate={reduce ? undefined : { scaleX: [1, (t.w - t.d) / t.w, 1] }}
            transition={{ duration: t.dur, repeat: Infinity, ease: "easeInOut", delay: i * 0.4 }}
          />
          <motion.circle
            cx={38 + t.w} cy={t.y + 3} r="7" fill="#ffffff" stroke="#6d28d9" strokeWidth={2}
            animate={reduce ? undefined : { x: [0, -t.d, 0] }}
            transition={{ duration: t.dur, repeat: Infinity, ease: "easeInOut", delay: i * 0.4 }}
          />
        </g>
      ))}
      <g transform="translate(112 112) rotate(-32)">
        <motion.g animate={reduce ? undefined : { y: [0, -4, 0] }} transition={{ duration: 4.4, repeat: Infinity, ease: "easeInOut" }}>
          <g transform="translate(2 3)" opacity={0.35}>
            <circle cx="0" cy="0" r="12" fill="none" stroke="#2e1065" strokeWidth={6} />
            <rect x="12" y="-3" width="46" height="6" rx="3" fill="#2e1065" />
          </g>
          <circle cx="0" cy="0" r="12" fill="none" stroke={`url(#${gid.kp_key})`} strokeWidth={6} />
          <rect x="12" y="-3" width="46" height="6" rx="3" fill={`url(#${gid.kp_key})`} />
          <rect x="40" y="3" width="5" height="10" rx="2" fill={`url(#${gid.kp_key})`} />
          <rect x="50" y="3" width="5" height="7" rx="2" fill={`url(#${gid.kp_key})`} />
        </motion.g>
      </g>
    </motion.svg>
  );
}
