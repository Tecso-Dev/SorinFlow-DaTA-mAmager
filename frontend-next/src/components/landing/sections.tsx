"use client";

// The three reading sections of the landing page: a wider introduction, who
// stands behind the product, and a middling-technical look at how it is built.
// Nothing here names a company or a person — only the brand, which comes from
// the settings — and nothing reveals a host, a version or an address.

import { GitBranch, Layers, Radio, ShieldCheck, Sparkles } from "lucide-react";
import { motion } from "motion/react";
import { useState } from "react";
import { cn } from "cn";
import { IsoBadge } from "@/components/panel/kit";
import { Reveal, Tilt } from "@/components/viz";
import { LANDING } from "@/content/landing";
import { faNum } from "@/lib/format";
import { usePrefersStill } from "@/lib/use-still";

const grad = "bg-linear-to-l from-indigo-600 via-violet-600 to-cyan-600 bg-clip-text text-transparent dark:from-indigo-300 dark:via-violet-300 dark:to-cyan-300";
const glass = "rounded-3xl border bg-card/70 backdrop-blur-xl shadow-[0_24px_60px_-30px_rgb(49_46_129/0.45)] dark:bg-white/[0.035] dark:shadow-[0_30px_80px_-30px_rgb(0_0_0/0.8)]";

function Head({ eyebrow, title, lead }: { eyebrow: string; title: readonly [string, string]; lead: string }) {
  return (
    <div className="flex flex-col gap-4">
      <Reveal>
        <div className="flex items-center gap-3 text-xs font-bold tracking-[0.3em] text-violet-600 dark:text-violet-300">
          {eyebrow}
          <span aria-hidden className="h-px w-16 bg-linear-to-l from-transparent to-current" />
        </div>
      </Reveal>
      <Reveal delay={0.08}>
        <h2 className="text-[clamp(2rem,5.5vw,4rem)] leading-[1.2] font-black tracking-tight">
          {title[0]} <span className={grad}>{title[1]}</span>
        </h2>
      </Reveal>
      <Reveal delay={0.16}>
        <p className="max-w-xl leading-8 text-muted-foreground">{lead}</p>
      </Reveal>
    </div>
  );
}

const block = "mx-auto max-w-7xl scroll-mt-20 px-4 py-[14vh] sm:px-[6vw]";

/* ───────────────────────── overview ───────────────────────── */

export function Overview() {
  const o = LANDING.overview;
  return (
    <section id="overview" className={block}>
      <Head eyebrow={o.eyebrow} title={o.title} lead={o.lead} />
      <div className="mt-12 grid grid-cols-1 gap-10 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
        <div className="flex flex-col gap-5">
          {o.paragraphs.map((t, i) => (
            <Reveal key={i} delay={i * 0.08}>
              <p className={cn("leading-9", i === 0 ? "text-lg text-foreground/90" : "text-muted-foreground")}>{t}</p>
            </Reveal>
          ))}
        </div>
        <ul className="flex flex-col gap-3">
          {o.points.map((p, i) => (
            <li key={p.title} className="h-full">
              <Reveal delay={0.1 + i * 0.08}>
                <Tilt className="rounded-2xl" max={5}>
                  <div className={cn(glass, "flex gap-4 rounded-2xl p-5")}>
                    <span className={cn("shrink-0 text-3xl leading-none font-black opacity-70", grad)}>{faNum(i + 1)}</span>
                    <div>
                      <h3 className="font-extrabold">{p.title}</h3>
                      <p className="mt-1 text-sm leading-7 text-muted-foreground">{p.text}</p>
                    </div>
                  </div>
                </Tilt>
              </Reveal>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

/* ───────────────────────── ownership ───────────────────────── */

const OWN_ICONS = [Sparkles, Radio, ShieldCheck];

export function Ownership({ brand }: { brand: string }) {
  const o = LANDING.ownership;
  return (
    <section id="ownership" className={block}>
      <div className="grid grid-cols-1 items-center gap-12 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
        <div>
          <Head eyebrow={o.eyebrow} title={o.title} lead={o.lead} />
          <ul className="mt-8 flex flex-col gap-3">
            {o.items.map((it, i) => {
              const Icon = OWN_ICONS[i];
              return (
                <li key={it.title}>
                  <Reveal delay={i * 0.08} className={cn(glass, "flex gap-3 rounded-2xl p-4")}>
                    <Icon className="mt-1 size-5 shrink-0 text-violet-600 dark:text-violet-300" aria-hidden />
                    <div>
                      <h3 className="font-extrabold">{it.title}</h3>
                      <p className="mt-1 text-sm leading-7 text-muted-foreground">{it.text}</p>
                    </div>
                  </Reveal>
                </li>
              );
            })}
          </ul>
          <Reveal delay={0.3}>
            <p className="mt-6 text-xs leading-6 text-muted-foreground">{o.note}</p>
          </Reveal>
        </div>
        <Reveal delay={0.12}>
          <Monogram brand={brand} />
        </Reveal>
      </div>
    </section>
  );
}

/** The brand's name on a slowly turning plinth — the only thing that stands in
 *  for an owner, because nothing else about one belongs on a public page. */
function Monogram({ brand }: { brand: string }) {
  const still = usePrefersStill();
  return (
    <div className="relative mx-auto grid aspect-square w-full max-w-[380px] place-items-center">
      <div aria-hidden className="absolute inset-[12%] rounded-full bg-[radial-gradient(circle_at_35%_30%,rgb(139_92_246/0.35),transparent_68%)] blur-2xl" />
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          aria-hidden
          className="absolute rounded-full border border-violet-500/25 dark:border-violet-300/20"
          style={{ inset: `${8 + i * 9}%` }}
          animate={still ? undefined : { rotate: i % 2 ? -360 : 360 }}
          transition={{ duration: 46 + i * 16, repeat: Infinity, ease: "linear" }}
        >
          <span className="absolute start-1/2 top-0 size-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-cyan-400/80" />
        </motion.span>
      ))}
      <motion.div
        className={cn(glass, "relative flex flex-col items-center gap-2 rounded-[2rem] px-8 py-7 text-center")}
        animate={still ? undefined : { y: [0, -9, 0] }}
        transition={{ duration: 6, repeat: Infinity, ease: "easeInOut" }}
      >
        <span className={cn("text-3xl leading-tight font-black sm:text-4xl", grad)}>{brand}</span>
        <span className="text-[11px] tracking-[0.3em] text-muted-foreground">۱۴۰۵</span>
      </motion.div>
    </div>
  );
}

/* ───────────────────────── technical ───────────────────────── */

export function Tech({ github }: { github: string }) {
  const t = LANDING.tech;
  const [active, setActive] = useState(0);
  return (
    <section id="tech" className={block}>
      <Head eyebrow={t.eyebrow} title={t.title} lead={t.lead} />
      <div className="mt-12 grid grid-cols-1 items-start gap-10 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
        <Reveal>
          <Stack active={active} />
        </Reveal>
        <div>
          <ol className="flex flex-col">
            {t.layers.map((l, i) => {
              const on = i === active;
              return (
                <li key={l.title} className="border-b">
                  <Reveal delay={i * 0.06}>
                    <button
                      type="button"
                      aria-expanded={on}
                      onMouseEnter={() => setActive(i)}
                      onFocus={() => setActive(i)}
                      onClick={() => setActive(i)}
                      className="flex w-full items-start gap-4 py-4 text-start outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <span
                        className={cn(
                          "mt-1 grid size-7 shrink-0 place-items-center rounded-lg border text-[11px] font-black transition",
                          on ? "border-transparent bg-linear-to-br from-indigo-500 to-violet-600 text-white" : "text-muted-foreground",
                        )}
                      >
                        {faNum(i + 1)}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className={cn("block text-lg font-extrabold transition", on ? grad : "text-muted-foreground")}>{l.title}</span>
                        <span className="mt-1 block text-sm leading-7 text-muted-foreground">{l.text}</span>
                      </span>
                    </button>
                  </Reveal>
                </li>
              );
            })}
          </ol>
          <ul className="mt-8 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {t.highlights.map((h, i) => (
              <li key={h.title} className="h-full">
                <Reveal delay={i * 0.05} className={cn(glass, "flex h-full gap-3 rounded-2xl p-4")}>
                  <ShieldCheck className="mt-0.5 size-4 shrink-0 text-cyan-600 dark:text-cyan-300" aria-hidden />
                  <div>
                    <h3 className="text-sm font-extrabold">{h.title}</h3>
                    <p className="mt-1 text-xs leading-6 text-muted-foreground">{h.text}</p>
                  </div>
                </Reveal>
              </li>
            ))}
          </ul>
          {github && (
            <Reveal delay={0.2}>
              <div className={cn(glass, "mt-6 flex flex-wrap items-center justify-between gap-4 rounded-2xl p-5")}>
                <span className="flex items-center gap-3 text-sm font-bold">
                  <IsoBadge icon={Layers} className="scale-[0.65]" />
                  {t.repo}
                </span>
                <a
                  href={github}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 rounded-full border bg-background/40 px-5 py-2.5 text-sm font-extrabold outline-none backdrop-blur transition hover:-translate-y-0.5 hover:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <GitBranch className="size-4" aria-hidden />
                  {t.repoCta}
                </a>
              </div>
            </Reveal>
          )}
        </div>
      </div>
    </section>
  );
}

/** The four layers as isometric plates, the chosen one lifted and lit, with a
 *  request travelling down the stack and its answer coming back up. */
function Stack({ active }: { active: number }) {
  const still = usePrefersStill();
  const GAP = 66, T = 13, TOP = 26;
  const top = "M100 0 L192 40 L100 80 L8 40 Z";
  const leftFace = `M8 40 L100 80 L100 ${80 + T} L8 ${40 + T} Z`;
  const rightFace = `M192 40 L100 80 L100 ${80 + T} L192 ${40 + T} Z`;
  return (
    <svg viewBox="0 0 200 330" className="mx-auto w-full max-w-[360px] overflow-visible" aria-hidden>
      <defs>
        <linearGradient id="st-on" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#a5b4fc" /><stop offset="0.55" stopColor="#6366f1" /><stop offset="1" stopColor="#22d3ee" />
        </linearGradient>
        <linearGradient id="st-off" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#4c4a8a" stopOpacity="0.55" /><stop offset="1" stopColor="#312e81" stopOpacity="0.55" />
        </linearGradient>
        <radialGradient id="st-glow">
          <stop offset="0" stopColor="#8b5cf6" stopOpacity="0.5" /><stop offset="1" stopColor="#8b5cf6" stopOpacity="0" />
        </radialGradient>
      </defs>
      <ellipse cx="100" cy="318" rx="86" ry="12" fill="url(#st-glow)" />
      {/* the spine the traffic runs along, drawn under the plates */}
      <line x1="100" y1={TOP + 40} x2="100" y2={TOP + 40 + GAP * 3} stroke="#6366f1" strokeOpacity="0.35" strokeWidth="2" strokeDasharray="4 5" />
      {!still && (
        <>
          {/* y, not cy: motion writes an animated SVG attribute as a bare
              value and can put "undefined" there for a frame while it sets
              the animation up — the browser then logs an error and the e2e
              specs count it as a page problem. A transform has no such
              state, so both dots sit at their start and travel by translate. */}
          <motion.circle
            r="4" fill="#22d3ee" cx="100" cy={TOP + 40}
            initial={{ y: 0, opacity: 0 }}
            animate={{ y: [0, GAP * 3], opacity: [0, 1, 1, 0] }}
            transition={{ duration: 2.6, repeat: Infinity, repeatDelay: 0.6, ease: "easeInOut" }}
          />
          <motion.circle
            r="3.5" fill="#f0abfc" cx="100" cy={TOP + 40 + GAP * 3}
            initial={{ y: 0, opacity: 0 }}
            animate={{ y: [0, -GAP * 3], opacity: [0, 1, 1, 0] }}
            transition={{ duration: 2.6, repeat: Infinity, repeatDelay: 0.6, delay: 1.3, ease: "easeInOut" }}
          />
        </>
      )}
      {[3, 2, 1, 0].map((i) => {
        const on = i === active;
        const y = TOP + i * GAP;
        return (
          <motion.g
            key={i}
            animate={still ? undefined : { y: on ? -9 : 0 }}
            transition={{ type: "spring", stiffness: 220, damping: 22 }}
          >
            <g transform={`translate(0 ${y})`}>
              <path d={leftFace} fill={on ? "#3730a3" : "#241f52"} />
              <path d={rightFace} fill={on ? "#312e81" : "#1b1740"} />
              <path d={top} fill={on ? "url(#st-on)" : "url(#st-off)"} />
              <path d={top} fill="none" stroke="#ffffff" strokeOpacity={on ? 0.65 : 0.18} strokeWidth="1.2" />
              {/* a few chips on the plate so it reads as a machine, not a slab */}
              {[[78, 34], [100, 45], [122, 34]].map(([cx, cy], k) => (
                <rect
                  key={k} x={cx - 9} y={cy - 4} width="18" height="8" rx="2"
                  transform={`rotate(${k === 1 ? 0 : k === 0 ? 24 : -24} ${cx} ${cy})`}
                  fill="#ffffff" fillOpacity={on ? 0.55 : 0.2}
                />
              ))}
            </g>
          </motion.g>
        );
      })}
    </svg>
  );
}
