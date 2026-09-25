"use client";

import {
  animate, motion, useAnimationFrame, useInView, useMotionTemplate, useMotionValue, useReducedMotion,
  useSpring, useTransform,
} from "motion/react";
import { useEffect, useRef, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, PolarAngleAxis, PolarGrid, Radar, RadarChart, XAxis, YAxis,
} from "recharts";
import { cn } from "cn";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { faNum, faPercent } from "@/lib/format";

const EASE = [0.22, 1, 0.36, 1] as const;

/* ───────────────────────── motion primitives ───────────────────────── */

/** Fades and lifts its content in the first time it scrolls into view. */
export function Reveal({ children, delay = 0, className }: { children: React.ReactNode; delay?: number; className?: string }) {
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 18 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "0px 0px -40px 0px" }}
      transition={{ duration: 0.55, delay, ease: EASE }}
    >
      {children}
    </motion.div>
  );
}

/** Counts up to `value` once it is on screen (skipped for reduced motion). */
export function CountUp({ value, digits = 0 }: { value: number; digits?: number }) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true });
  const reduce = useReducedMotion();
  const [n, setN] = useState(value);
  useEffect(() => {
    if (!inView || reduce) return;
    const c = animate(0, value, { duration: 1.2, ease: EASE, onUpdate: setN });
    return () => c.stop();
  }, [inView, reduce, value]);
  return (
    <span ref={ref} className="tabular">
      {faNum(n, { maximumFractionDigits: digits, minimumFractionDigits: digits })}
    </span>
  );
}

/** A card that leans toward the mouse in 3D, with a soft glare. Mouse only:
 *  touch and reduced-motion users get a still card. */
export function Tilt({ children, className, max = 7 }: { children: React.ReactNode; className?: string; max?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();
  const px = useMotionValue(0.5);
  const py = useMotionValue(0.5);
  const spring = { stiffness: 220, damping: 22, mass: 0.6 };
  const rotateX = useSpring(useTransform(py, [0, 1], [max, -max]), spring);
  const rotateY = useSpring(useTransform(px, [0, 1], [-max, max]), spring);
  const gx = useTransform(px, (v) => `${v * 100}%`);
  const gy = useTransform(py, (v) => `${v * 100}%`);
  const glare = useMotionTemplate`radial-gradient(circle at ${gx} ${gy}, rgb(255 255 255 / 0.16), transparent 60%)`;

  function onMove(e: React.PointerEvent) {
    if (reduce || e.pointerType !== "mouse" || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    px.set((e.clientX - r.left) / r.width);
    py.set((e.clientY - r.top) / r.height);
  }
  function onLeave() {
    px.set(0.5);
    py.set(0.5);
  }

  return (
    <motion.div
      ref={ref}
      onPointerMove={onMove}
      onPointerLeave={onLeave}
      style={{ rotateX, rotateY, transformPerspective: 900 }}
      whileHover={reduce ? undefined : { scale: 1.015 }}
      transition={{ type: "spring", ...spring }}
      className={cn("group/tilt relative will-change-transform", className)}
    >
      {children}
      <motion.div
        aria-hidden
        className="pointer-events-none absolute inset-0 rounded-[inherit] opacity-0 transition-opacity duration-300 group-hover/tilt:opacity-100"
        style={{ background: glare }}
      />
    </motion.div>
  );
}

function useProgress(active: boolean, duration = 1.4) {
  const reduce = useReducedMotion();
  const [p, setP] = useState(0);
  useEffect(() => {
    if (!active || reduce) return;
    const c = animate(0, 1, { duration, ease: EASE, onUpdate: setP });
    return () => c.stop();
  }, [active, reduce, duration]);
  return reduce ? 1 : p;
}

/* ───────────────────────── 3D skyline (isometric districts) ───────────────────────── */

const COS30 = Math.cos(Math.PI / 6);
const SIN30 = Math.sin(Math.PI / 6);
const iso = (x: number, y: number, z: number, s: number): [number, number] => [(x - y) * COS30 * s, (x + y) * SIN30 * s - z * s];
const pts = (list: [number, number][]) => list.map(([a, b]) => `${a.toFixed(1)},${b.toFixed(1)}`).join(" ");
const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

export type District = { name: string; count: number; ppm: number; delta: number };

/** Each district is a building: height = active listings, colour = price per
 *  square metre, from --viz-low (cheaper) to --viz-high (dearer). */
export function Skyline({ data, compact = false }: { data: District[]; compact?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "0px 0px -60px 0px" });
  const p = useProgress(inView, 1.6);
  const [hover, setHover] = useState<number | null>(null);

  const s = compact ? 30 : 44;
  const cols = 3;
  const gap = 1.75;
  const maxCount = Math.max(...data.map((d) => d.count));
  const minP = Math.min(...data.map((d) => d.ppm));
  const maxP = Math.max(...data.map((d) => d.ppm));
  const H = 2.7;

  const blocks = data.map((d, i) => {
    const x0 = (i % cols) * gap;
    const y0 = Math.floor(i / cols) * gap;
    const t = (d.ppm - minP) / Math.max(1, maxP - minP);
    const grow = clamp01((p * 1.35 - i * 0.07) / 1);
    const h = Math.max(0.05, (d.count / maxCount) * H * (1 - Math.pow(1 - grow, 3)));
    const base = `color-mix(in oklab, var(--viz-high) ${Math.round(t * 100)}%, var(--viz-low))`;
    return { d, i, x0, y0, h, base };
  });

  // Painter's order: farther buildings first.
  const order = [...blocks].sort((a, b) => a.x0 + a.y0 - (b.x0 + b.y0));

  const rows = Math.ceil(data.length / cols);
  const ext = { x: (cols - 1) * gap + 1, y: (rows - 1) * gap + 1 };
  const m = 0.55;
  const ground: [number, number][] = [
    iso(-m, -m, 0, s), iso(ext.x + m, -m, 0, s), iso(ext.x + m, ext.y + m, 0, s), iso(-m, ext.y + m, 0, s),
  ];
  const xs = ground.map((g) => g[0]);
  const top = iso(0, 0, H, s)[1] - 26;
  const vb = { x: Math.min(...xs) - 8, y: top, w: Math.max(...xs) - Math.min(...xs) + 16, h: ground[2][1] - top + 8 };

  return (
    <div ref={ref} className="flex flex-col gap-4 md:flex-row md:items-center">
      <svg
        viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`}
        className={cn("w-full md:w-[62%]", compact ? "max-h-[180px]" : "max-h-[300px]")}
        role="img"
        aria-label="نمای سه‌بعدی آگهی‌های فعال محله‌ها"
      >
        <polygon points={pts(ground)} className="fill-muted stroke-border" strokeWidth={1} />
        {Array.from({ length: 7 }, (_, k) => {
          const f = k / 6;
          const a = iso(-m + f * (ext.x + 2 * m), -m, 0, s);
          const b = iso(-m + f * (ext.x + 2 * m), ext.y + m, 0, s);
          return <line key={k} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} className="stroke-border" strokeWidth={0.6} />;
        })}
        {order.map(({ d, i, x0, y0, h, base }) => {
          const w = 1;
          const faded = hover !== null && hover !== i;
          const topFace = pts([iso(x0, y0, h, s), iso(x0 + w, y0, h, s), iso(x0 + w, y0 + w, h, s), iso(x0, y0 + w, h, s)]);
          const left = pts([iso(x0, y0 + w, 0, s), iso(x0 + w, y0 + w, 0, s), iso(x0 + w, y0 + w, h, s), iso(x0, y0 + w, h, s)]);
          const right = pts([iso(x0 + w, y0, 0, s), iso(x0 + w, y0 + w, 0, s), iso(x0 + w, y0 + w, h, s), iso(x0 + w, y0, h, s)]);
          const floors = Math.floor(h / 0.32);
          const label = iso(x0 + w / 2, y0 + w / 2, h, s);
          const foot = iso(x0 + w / 2, y0 + w + 0.42, 0, s);
          return (
            <g
              key={d.name}
              onPointerEnter={() => setHover(i)}
              onPointerLeave={() => setHover(null)}
              className="cursor-pointer transition-[opacity,transform] duration-300"
              style={{ opacity: faded ? 0.4 : 1, transform: hover === i ? "translateY(-6px)" : undefined }}
            >
              <polygon points={left} style={{ fill: base }} />
              <polygon points={right} style={{ fill: `color-mix(in oklab, ${base} 68%, black)` }} />
              {Array.from({ length: floors }, (_, k) => {
                const z = (k + 1) * 0.32;
                const a = iso(x0, y0 + w, z, s);
                const b = iso(x0 + w, y0 + w, z, s);
                const c = iso(x0 + w, y0, z, s);
                return (
                  <polyline key={k} points={pts([a, b, c])} fill="none" stroke="white" strokeOpacity={0.18} strokeWidth={0.8} />
                );
              })}
              <polygon points={topFace} style={{ fill: `color-mix(in oklab, ${base} 78%, white)` }} />
              <text x={label[0]} y={label[1] - 8} textAnchor="middle" className="fill-foreground text-[11px] font-bold">
                {faNum(d.count)}
              </text>
              {!compact && (
                <text x={foot[0]} y={foot[1] + 4} textAnchor="middle" className="fill-muted-foreground text-[10px]">
                  {d.name}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {!compact && (
        <ul className="flex flex-1 flex-col gap-1.5 text-sm">
          {data.map((d, i) => (
            <li
              key={d.name}
              onPointerEnter={() => setHover(i)}
              onPointerLeave={() => setHover(null)}
              className={cn(
                "flex items-center gap-2 rounded-lg px-2 py-1.5 transition-colors",
                hover === i && "bg-accent",
              )}
            >
              <span
                className="size-2.5 shrink-0 rounded-sm"
                style={{ background: blocks[i].base }}
              />
              <span className="flex-1 truncate font-medium">{d.name}</span>
              <span className="text-xs text-muted-foreground tabular">{faNum(d.count)} آگهی</span>
              <span className="w-14 text-end font-semibold tabular">{faNum(d.ppm)}</span>
            </li>
          ))}
          <li className="px-2 pt-1 text-[11px] text-muted-foreground">عدد آخر: میانگین هر متر، میلیون تومان</li>
        </ul>
      )}
    </div>
  );
}

/* ───────────────────────── 3D donut (layered extrusion) ───────────────────────── */

export type Slice = { label: string; value: number; color: string };

function DonutLayer({ data, r, stroke }: { data: Slice[]; r: number; stroke: number }) {
  const total = data.reduce((a, d) => a + d.value, 0);
  const c = 2 * Math.PI * r;
  const lens = data.map((d) => (d.value / total) * c);
  const starts = lens.map((_, i) => lens.slice(0, i).reduce((a, b) => a + b, 0));
  return (
    <>
      {data.map((d, i) => (
        <circle
          key={d.label}
          cx={100}
          cy={100}
          r={r}
          fill="none"
          stroke={d.color}
          strokeWidth={stroke}
          strokeDasharray={`${Math.max(lens[i] - 2, 0)} ${c}`}
          strokeDashoffset={-starts[i]}
          transform="rotate(-90 100 100)"
        />
      ))}
    </>
  );
}

/** A thick ring seen at an angle: the same donut stacked a few pixels apart in
 *  3D, the lower copies darker. Spins slowly unless motion is reduced. */
export function Donut3D({ data, centerLabel }: { data: Slice[]; centerLabel: string }) {
  const reduce = useReducedMotion();
  const spin = useMotionValue(0);
  useAnimationFrame((t) => {
    if (!reduce) spin.set((t / 1000) * 5);
  });
  const transform = useMotionTemplate`rotateX(58deg) rotateZ(${spin}deg)`;
  const total = data.reduce((a, d) => a + d.value, 0);
  const layers = 11;

  return (
    <div className="flex flex-col items-center gap-4 sm:flex-row lg:flex-col 2xl:flex-row">
      <div className="relative h-[190px] w-[230px] shrink-0 [perspective:800px]">
        <motion.div className="absolute inset-0 [transform-style:preserve-3d]" style={{ transform }}>
          {Array.from({ length: layers }, (_, i) => (
            <svg
              key={i}
              viewBox="0 0 200 200"
              className="absolute inset-0 m-auto size-[190px]"
              style={{
                transform: `translateZ(${i * 1.7}px)`,
                filter: i === layers - 1 ? undefined : `brightness(${0.42 + i * 0.045})`,
              }}
              aria-hidden
            >
              <DonutLayer data={data} r={70} stroke={30} />
            </svg>
          ))}
        </motion.div>
        {/* The ring's top layer sits ~15px above its base on screen, and the
            tilted hole is only ~55px tall: the number alone fits there. */}
        <div className="pointer-events-none absolute inset-0 grid place-items-center">
          <div className="-mt-[30px] text-xl font-black tabular">{faNum(total)}</div>
        </div>
        <div className="absolute inset-x-0 bottom-0 text-center text-[11px] text-muted-foreground">{centerLabel}</div>
      </div>
      <ul className="flex w-full flex-col gap-2 text-sm">
        {data.map((d) => (
          <li key={d.label} className="flex items-center gap-2">
            <span className="size-2.5 shrink-0 rounded-full" style={{ background: d.color }} />
            <span className="flex-1 truncate">{d.label}</span>
            <span className="text-xs text-muted-foreground tabular">{faNum(d.value)}</span>
            <span className="w-11 text-end font-semibold tabular">{faPercent((d.value / total) * 100, 0)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ───────────────────────── call heatmap ───────────────────────── */

const DAYS = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"];
const HOURS = Array.from({ length: 12 }, (_, i) => 8 + i);

export function CallHeatmap({ grid }: { grid: number[][] }) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true });
  const max = Math.max(...grid.flat());
  return (
    <div ref={ref} className="overflow-x-auto">
      <div className="grid min-w-[520px] grid-cols-[4.5rem_repeat(12,minmax(0,1fr))] gap-1 text-[10px]">
        <span />
        {HOURS.map((h) => (
          <span key={h} className="pb-1 text-center text-muted-foreground tabular">
            {faNum(h)}
          </span>
        ))}
        {grid.map((row, d) => (
          <div key={DAYS[d]} className="contents">
            <span className="flex items-center text-xs text-muted-foreground">{DAYS[d]}</span>
            {row.map((v, h) => (
              <motion.span
                key={h}
                title={`${DAYS[d]}، ساعت ${faNum(HOURS[h])}: ${faNum(v)} تماس`}
                initial={{ opacity: 0, scale: 0.6 }}
                animate={inView ? { opacity: 1, scale: 1 } : undefined}
                transition={{ duration: 0.35, delay: (d * 12 + h) * 0.006, ease: EASE }}
                className="aspect-square rounded-[5px] vb:rounded-[3px] vc:rounded-lg"
                style={{ background: `color-mix(in oklab, var(--primary) ${Math.round((v / max) * 92) + 4}%, var(--muted))` }}
              />
            ))}
          </div>
        ))}
      </div>
      <div className="mt-3 flex items-center justify-end gap-1.5 text-[10px] text-muted-foreground">
        کم
        {[8, 30, 55, 80, 96].map((p) => (
          <span key={p} className="size-3 rounded-[3px]" style={{ background: `color-mix(in oklab, var(--primary) ${p}%, var(--muted))` }} />
        ))}
        زیاد
      </div>
    </div>
  );
}

/* ───────────────────────── deals by month ───────────────────────── */

const dealsConfig = {
  sale: { label: "خرید و فروش", color: "var(--chart-1)" },
  rent: { label: "رهن و اجاره", color: "var(--chart-3)" },
  presale: { label: "پیش‌فروش", color: "var(--chart-2)" },
} satisfies ChartConfig;

export function DealsByMonth({ data }: { data: { month: string; sale: number; rent: number; presale: number }[] }) {
  return (
    <>
      <div className="mb-3 flex flex-wrap gap-4 text-xs text-muted-foreground">
        {Object.entries(dealsConfig).map(([k, c]) => (
          <span key={k} className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm" style={{ background: c.color }} />
            {c.label}
          </span>
        ))}
      </div>
      <ChartContainer config={dealsConfig} className="aspect-auto h-[230px] w-full">
        <BarChart data={data} margin={{ top: 4, left: 4, right: 4, bottom: 0 }} barSize={26}>
          <CartesianGrid vertical={false} strokeDasharray="3 3" />
          <XAxis dataKey="month" reversed tickLine={false} axisLine={false} tickMargin={8} />
          <YAxis orientation="right" tickLine={false} axisLine={false} width={28} tickFormatter={(v: number) => faNum(v)} />
          <ChartTooltip cursor={{ fill: "var(--muted)", opacity: 0.5 }} content={<ChartTooltipContent indicator="dot" />} />
          <Bar dataKey="sale" stackId="d" fill="var(--color-sale)" radius={[0, 0, 4, 4]} />
          <Bar dataKey="rent" stackId="d" fill="var(--color-rent)" />
          <Bar dataKey="presale" stackId="d" fill="var(--color-presale)" radius={[6, 6, 0, 0]} />
        </BarChart>
      </ChartContainer>
    </>
  );
}

/* ───────────────────────── team radar ───────────────────────── */

const radarConfig = {
  best: { label: "سارا احمدی", color: "var(--chart-1)" },
  avg: { label: "میانگین تیم", color: "var(--chart-3)" },
} satisfies ChartConfig;

export function TeamRadar({ data }: { data: { metric: string; best: number; avg: number }[] }) {
  return (
    <>
      <ChartContainer config={radarConfig} className="mx-auto aspect-square max-h-[260px] w-full">
        <RadarChart data={data} outerRadius="72%">
          <ChartTooltip content={<ChartTooltipContent indicator="line" />} />
          <PolarGrid className="stroke-border" />
          <PolarAngleAxis dataKey="metric" tick={{ fontSize: 11 }} />
          <Radar dataKey="avg" stroke="var(--color-avg)" fill="var(--color-avg)" fillOpacity={0.18} strokeWidth={1.5} />
          <Radar dataKey="best" stroke="var(--color-best)" fill="var(--color-best)" fillOpacity={0.32} strokeWidth={2} />
        </RadarChart>
      </ChartContainer>
      <div className="flex justify-center gap-4 text-xs text-muted-foreground">
        {Object.entries(radarConfig).map(([k, c]) => (
          <span key={k} className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-full" style={{ background: c.color }} />
            {c.label}
          </span>
        ))}
      </div>
    </>
  );
}

/* ───────────────────────── monthly target gauge ───────────────────────── */

/** Two half-rings that draw themselves, filling right to left. */
export function TargetGauge({ items }: { items: { label: string; value: number; target: number; unit: string; color: string }[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true });
  const reduce = useReducedMotion();
  const main = items[0];
  const pct = (main.value / main.target) * 100;
  const arc = (r: number) => `M ${120 + r} 120 A ${r} ${r} 0 0 0 ${120 - r} 120`;
  return (
    <div ref={ref} className="flex flex-col items-center">
      <svg viewBox="0 0 240 132" className="w-full max-w-[300px]" aria-hidden>
        {items.map((it, i) => {
          const r = 100 - i * 26;
          return (
            <g key={it.label}>
              <path d={arc(r)} fill="none" className="stroke-muted" strokeWidth={16} strokeLinecap="round" />
              <motion.path
                d={arc(r)}
                fill="none"
                stroke={it.color}
                strokeWidth={16}
                strokeLinecap="round"
                initial={{ pathLength: reduce ? it.value / it.target : 0 }}
                animate={inView ? { pathLength: Math.min(1, it.value / it.target) } : undefined}
                transition={{ duration: 1.4, delay: 0.15 + i * 0.2, ease: EASE }}
              />
            </g>
          );
        })}
      </svg>
      <div className="-mt-12 text-center">
        <div className="text-3xl font-black tabular">
          <CountUp value={Math.round(pct)} />٪
        </div>
        <div className="text-xs text-muted-foreground">از هدف قرارداد این ماه</div>
      </div>
      <ul className="mt-5 grid w-full gap-2 text-sm">
        {items.map((it) => (
          <li key={it.label} className="flex items-center gap-2">
            <span className="size-2.5 shrink-0 rounded-full" style={{ background: it.color }} />
            <span className="flex-1">{it.label}</span>
            <span className="tabular">
              <span className="font-bold">{faNum(it.value)}</span>
              <span className="text-muted-foreground"> از {faNum(it.target)} {it.unit}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
