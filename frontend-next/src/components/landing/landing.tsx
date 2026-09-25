"use client";

// The public landing page at /. The words live in content/landing.ts and the
// brand, contact details and live numbers come from the server as props, so
// nothing here spells the brand. Every section moves (reveal, count-up, tilt)
// and carries a 3D piece; the particle scene behind them is nebula.tsx.

import {
  ArrowLeft, Bell, BrainCircuit, Building2, FileSearch, Fingerprint, KeyRound, Lock, Mail, MessageSquareText,
  Phone, ScanSearch, Send, ShieldCheck, UserRoundCog, Users, Workflow,
} from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import Link from "next/link";
import { useState } from "react";
import { cn } from "cn";
import { Logo, LogoMark } from "@/components/brand/logo";
import { IsoBadge } from "@/components/panel/kit";
import { CountUp, Reveal, Tilt } from "@/components/viz";
import { fill, LANDING } from "@/content/landing";
import { faNum } from "@/lib/format";
import { Nebula, type NebulaAnchor } from "./nebula";

export type LandingSite = {
  brandName: string;
  tagline: string;
  domain: string;
  phone: string;
  email: string;
  telegram: string;
};
export type LandingStats = { total_properties: number; total_leads: number; phone_rate: number; dpa_today_top: number } | null;

const EASE = [0.22, 1, 0.36, 1] as const;

// Which particle formation each section shows as it scrolls into view.
const ANCHORS: NebulaAnchor[] = [
  { id: "top", formation: "nebula" },
  { id: "features", formation: "city" },
  { id: "how", formation: "house" },
  { id: "ai", formation: "house" },
  { id: "security", formation: "key" },
  { id: "contact", formation: "infinity" },
];

const grad = "bg-linear-to-l from-indigo-600 via-violet-600 to-cyan-600 bg-clip-text text-transparent dark:from-indigo-300 dark:via-violet-300 dark:to-cyan-300";
const glass = "rounded-3xl border bg-card/70 backdrop-blur-xl shadow-[0_24px_60px_-30px_rgb(49_46_129/0.45)] dark:bg-white/[0.035] dark:shadow-[0_30px_80px_-30px_rgb(0_0_0/0.8)]";

export function Landing({
  site, stats, portalOpen, year,
}: { site: LandingSite; stats: LandingStats; portalOpen: boolean; year: string }) {
  const brand = site.brandName;
  return (
    <div className="relative overflow-x-clip bg-background text-foreground">
      <Nav brand={brand} portalOpen={portalOpen} />
      <Nebula anchors={ANCHORS} />
      <main className="relative z-10">
        <Hero portalOpen={portalOpen} />
        <Stats stats={stats} />
        <Features stats={stats} domain={site.domain} />
        <How />
        <Ai />
        <Security />
        <Contact site={site} />
      </main>
      <Footer brand={brand} tagline={site.tagline} portalOpen={portalOpen} year={year} />
    </div>
  );
}

/* ───────────────────────── nav ───────────────────────── */

function Nav({ brand, portalOpen }: { brand: string; portalOpen: boolean }) {
  const pill = "rounded-full border bg-background/60 px-4 py-2 text-sm font-bold backdrop-blur-xl transition hover:-translate-y-0.5 hover:border-primary/50 focus-visible:ring-2 focus-visible:ring-ring outline-none";
  return (
    <header className="fixed inset-x-0 top-0 z-40 bg-linear-to-b from-background from-60% to-transparent px-4 pt-4 pb-6 sm:px-6">
      <nav aria-label="اصلی" className="mx-auto flex max-w-7xl items-center justify-between gap-3">
        <Link href="/" aria-label={brand} className="rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <Logo name={brand} size={36} nameClassName="text-base font-black" />
        </Link>
        <div className="flex items-center gap-2">
          {LANDING.nav.map((n) => (
            <a key={n.href} href={n.href} className={cn(pill, "hidden text-muted-foreground hover:text-foreground lg:inline-flex")}>
              {n.label}
            </a>
          ))}
          {portalOpen && (
            <Link href="/portal" className={cn(pill, "hidden sm:inline-flex")}>
              {LANDING.footer.portal}
            </Link>
          )}
          <Link href="/panel/login" className={cn(pill, "border-transparent bg-foreground text-background hover:shadow-[0_8px_30px_-6px_rgb(99_102_241/0.6)]")}>
            ورود
          </Link>
        </div>
      </nav>
    </header>
  );
}

/* ───────────────────────── hero ───────────────────────── */

function Hero({ portalOpen }: { portalOpen: boolean }) {
  const h = LANDING.hero;
  const reduce = useReducedMotion();
  // Transform only (no fade) on the headline: it is the page's largest paint.
  const rise = (i: number) => ({
    initial: reduce ? false : { y: 60 },
    animate: { y: 0 },
    transition: { duration: 0.8, delay: i * 0.12, ease: EASE },
  });
  return (
    <section id="top" className="relative flex min-h-dvh flex-col justify-center px-4 pt-28 pb-16 sm:px-[6vw]">
      <h1 className="text-[clamp(3rem,12vw,9.5rem)] leading-[1.08] font-black tracking-tight">
        <motion.span className="block" {...rise(0)}>{h.lines[0]}</motion.span>
        <motion.span className={cn("block pb-2", grad)} {...rise(1)}>{h.lines[1]}</motion.span>
        <motion.span className="block text-transparent [-webkit-text-stroke:2px_var(--foreground)]" {...rise(2)}>
          {h.lines[2]}
        </motion.span>
      </h1>
      <motion.div
        initial={reduce ? false : { opacity: 0, y: 30 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.9, delay: 0.5, ease: EASE }}
        className="mt-10 flex flex-col gap-8 lg:flex-row lg:items-end lg:justify-between"
      >
        <div className="flex flex-wrap gap-3">
          {portalOpen && (
            <Link href="/portal" className={btn("solid")}>
              {h.portal} <ArrowLeft className="size-4" aria-hidden />
            </Link>
          )}
          <Link href="/panel/login" className={btn(portalOpen ? "line" : "solid")}>
            {h.panel} <ArrowLeft className="size-4" aria-hidden />
          </Link>
          <a href="#features" className={btn("line")}>{h.more}</a>
        </div>
        <p className="max-w-sm text-sm leading-7 text-muted-foreground">{h.side}</p>
      </motion.div>
      <div aria-hidden className="absolute bottom-6 left-1/2 hidden -translate-x-1/2 sm:block">
        <motion.div
          className="h-10 w-6 rounded-full border-2 border-muted-foreground/50"
          animate={reduce ? undefined : { y: [0, 6, 0] }}
          transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
        >
          <div className="mx-auto mt-2 h-2 w-1 rounded-full bg-muted-foreground/70" />
        </motion.div>
      </div>
    </section>
  );
}

function btn(kind: "solid" | "line") {
  return cn(
    "inline-flex items-center gap-2 rounded-full px-7 py-3.5 text-sm font-extrabold outline-none transition focus-visible:ring-2 focus-visible:ring-ring",
    kind === "solid"
      ? "bg-primary text-primary-foreground shadow-[0_12px_36px_-10px_rgb(99_102_241/0.8)] hover:-translate-y-0.5 hover:shadow-[0_16px_44px_-8px_rgb(99_102_241/0.9)]"
      : "border bg-background/40 backdrop-blur hover:-translate-y-0.5 hover:border-primary/50",
  );
}

/* ───────────────────────── section frame ───────────────────────── */

function Heading({ eyebrow, title, lead, center }: { eyebrow: string; title: readonly [string, string]; lead?: string; center?: boolean }) {
  return (
    <div className={cn("flex flex-col gap-4", center && "items-center text-center")}>
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
      {lead && (
        <Reveal delay={0.16}>
          <p className="max-w-xl leading-8 text-muted-foreground">{lead}</p>
        </Reveal>
      )}
    </div>
  );
}

function Block({ id, children, className }: { id: string; children: React.ReactNode; className?: string }) {
  return (
    <section id={id} className={cn("mx-auto max-w-7xl scroll-mt-20 px-4 py-[14vh] sm:px-[6vw]", className)}>
      {children}
    </section>
  );
}

/* ───────────────────────── stats ───────────────────────── */

function Stats({ stats }: { stats: LandingStats }) {
  const s = LANDING.stats;
  const items = [
    { v: stats?.total_properties, label: s.properties },
    { v: stats?.total_leads, label: s.leads },
    { v: stats?.phone_rate, label: s.phoneRate },
    { v: 24, label: s.hours },
  ];
  return (
    <div className="mx-auto max-w-7xl px-4 sm:px-[6vw]">
      <div className={cn(glass, "grid grid-cols-2 gap-px overflow-hidden bg-border md:grid-cols-4 dark:bg-white/[0.07]")}>
        {items.map((it, i) => (
          <Reveal key={it.label} delay={i * 0.08} className="bg-card/90 p-6 text-center backdrop-blur-xl dark:bg-[#07070d]/85">
            <div className={cn("text-3xl font-black sm:text-4xl", grad)}>
              {it.v == null ? "—" : <CountUp value={it.v} />}
            </div>
            <div className="mt-1 text-xs text-muted-foreground sm:text-sm">{it.label}</div>
          </Reveal>
        ))}
      </div>
    </div>
  );
}

/* ───────────────────────── features ───────────────────────── */

const FEATURE_ICONS = { scraper: ScanSearch, crm: Users, customers: UserRoundCog, alerts: Bell } as const;

function Features({ stats, domain }: { stats: LandingStats; domain: string }) {
  const f = LANDING.features;
  const [active, setActive] = useState(0);
  return (
    <Block id="features">
      <Heading eyebrow={f.eyebrow} title={f.title} lead={f.lead} />
      <div className="mt-12 grid grid-cols-1 items-center gap-10 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <ul className="flex flex-col">
          {f.items.map((it, i) => {
            const Icon = FEATURE_ICONS[it.key];
            const on = i === active;
            return (
              <Reveal key={it.key} delay={i * 0.06}>
                <li className="border-b">
                  <button
                    type="button"
                    aria-expanded={on}
                    onMouseEnter={() => setActive(i)}
                    onFocus={() => setActive(i)}
                    onClick={() => setActive(i)}
                    className="flex w-full items-center gap-4 py-5 text-start outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <IsoBadge icon={Icon} className={cn("transition", on ? "scale-90" : "scale-75 opacity-60 grayscale")} />
                    <span className="min-w-0 flex-1">
                      <span className={cn("block text-lg font-extrabold sm:text-2xl", on ? grad : "text-muted-foreground")}>{it.title}</span>
                      <motion.span
                        initial={false}
                        animate={{ height: on ? "auto" : 0, opacity: on ? 1 : 0 }}
                        transition={{ duration: 0.35, ease: EASE }}
                        className="block overflow-hidden text-sm leading-7 text-muted-foreground"
                      >
                        {it.text}
                      </motion.span>
                    </span>
                    <span className="text-xs tracking-widest text-muted-foreground/70 tabular-nums">{faNum(i + 1, { minimumIntegerDigits: 2 })}</span>
                  </button>
                </li>
              </Reveal>
            );
          })}
        </ul>
        <Reveal delay={0.1}>
          <Tilt className="rounded-3xl">
            <MockPanel stats={stats} domain={domain} />
          </Tilt>
        </Reveal>
      </div>
    </Block>
  );
}

const BARS = [38, 62, 47, 82, 58, 92, 70];

function MockPanel({ stats, domain }: { stats: LandingStats; domain: string }) {
  const reduce = useReducedMotion();
  const n = (v: number | undefined) => (v == null ? "—" : faNum(v));
  return (
    <div aria-hidden className={cn(glass, "relative aspect-[16/11] overflow-hidden p-4 sm:p-5")}>
      <div className="absolute inset-x-[-30%] -bottom-1/2 h-4/5 bg-[radial-gradient(ellipse_at_center,rgb(139_92_246/0.35),transparent_65%)]" />
      <div className="relative flex h-full flex-col gap-3">
        <div className="flex items-center gap-1.5" dir="ltr">
          <i className="size-2.5 rounded-full bg-fuchsia-400" />
          <i className="size-2.5 rounded-full bg-violet-400" />
          <i className="size-2.5 rounded-full bg-cyan-400" />
          <span className="ms-2 h-5 flex-1 truncate rounded-full border bg-background/40 px-3 text-[10px] leading-5 text-muted-foreground">
            {domain}/panel
          </span>
        </div>
        <div className="grid grid-cols-3 gap-2">
          {[
            [n(stats?.total_properties), LANDING.stats.properties],
            [n(stats?.total_leads), "لید فعال"],
            [stats?.dpa_today_top ? faNum(stats.dpa_today_top) : "—", "امتیاز DPA امروز"],
          ].map(([v, l]) => (
            <div key={l} className="rounded-xl border bg-background/40 p-2">
              <b className={cn("block text-sm font-black sm:text-lg", grad)}>{v}</b>
              <span className="text-[9px] text-muted-foreground sm:text-[10px]">{l}</span>
            </div>
          ))}
        </div>
        <div className="grid min-h-0 flex-1 grid-cols-[1.1fr_0.9fr] gap-2">
          <div className="flex items-end gap-1.5 rounded-xl border bg-background/30 p-3">
            {BARS.map((h, i) => (
              <motion.i
                key={i}
                className="flex-1 origin-bottom rounded-t-md bg-linear-to-t from-cyan-400/50 via-violet-500 to-fuchsia-400"
                style={{ height: `${h}%` }}
                animate={reduce ? undefined : { scaleY: [0.82, 1.06, 0.82] }}
                transition={{ duration: 2.6, repeat: Infinity, delay: i * 0.2, ease: "easeInOut" }}
              />
            ))}
          </div>
          <div className="flex min-h-0 flex-col gap-2">
            {["۷ میلیارد", "لید جدید", "بازدید"].map((t) => (
              <div key={t} className="flex flex-1 items-center gap-2 overflow-hidden rounded-xl border bg-background/40 px-2">
                <span className="size-6 shrink-0 rounded-md bg-linear-to-br from-violet-400/60 to-cyan-300/40" />
                <span className="flex min-w-0 flex-1 flex-col gap-1">
                  <u className="block h-1.5 w-4/5 rounded bg-foreground/15" />
                  <u className="block h-1.5 w-1/2 rounded bg-foreground/10" />
                </span>
                <span className="shrink-0 rounded-full border border-cyan-500/40 bg-cyan-500/10 px-1.5 text-[8px] font-bold text-cyan-700 sm:text-[9px] dark:text-cyan-300">
                  {t}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ───────────────────────── how it works: an isometric staircase ───────────────────────── */

const STEP_ICONS = [FileSearch, Building2, Workflow, KeyRound];

function How() {
  const h = LANDING.how;
  return (
    <Block id="how">
      <Heading eyebrow={h.eyebrow} title={h.title} />
      <ol className="mt-14 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4 lg:items-end lg:[--stair:28px]">
        {h.steps.map((s, i) => {
          const Icon = STEP_ICONS[i];
          return (
            <Reveal key={s.title} delay={i * 0.1}>
              {/* each step stands one stair higher than the last */}
              <li className="relative" style={{ marginBottom: `calc(var(--stair, 0px) * ${i})` }}>
                <Tilt className="rounded-3xl">
                  <div className={cn(glass, "relative p-6 shadow-[5px_7px_0_0_rgb(99_102_241/0.28),9px_13px_0_0_rgb(79_70_229/0.14)] dark:shadow-[5px_7px_0_0_rgb(49_46_129/0.75),9px_13px_0_0_rgb(30_27_75/0.8)]")}>
                    <div className="flex items-center justify-between">
                      <IsoBadge icon={Icon} className="scale-90" />
                      <span className={cn("text-5xl font-black opacity-80", grad)}>{faNum(i + 1)}</span>
                    </div>
                    <h3 className="mt-5 text-xl font-extrabold">{s.title}</h3>
                    <p className="mt-2 text-sm leading-7 text-muted-foreground">{s.text}</p>
                  </div>
                </Tilt>
              </li>
            </Reveal>
          );
        })}
      </ol>
    </Block>
  );
}

/* ───────────────────────── AI ───────────────────────── */

const AI_ICONS = [FileSearch, MessageSquareText, BrainCircuit, Send];

function Ai() {
  const a = LANDING.ai;
  const reduce = useReducedMotion();
  return (
    <Block id="ai">
      <div className="grid grid-cols-1 items-center gap-12 lg:grid-cols-2">
        <div>
          <Heading eyebrow={a.eyebrow} title={a.title} lead={a.lead} />
          <ul className="mt-8 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {a.items.map((it, i) => {
              const Icon = AI_ICONS[i];
              return (
                <Reveal key={it.title} delay={i * 0.07}>
                  <li className={cn(glass, "flex h-full gap-3 rounded-2xl p-4")}>
                    <Icon className="mt-1 size-5 shrink-0 text-violet-600 dark:text-violet-300" aria-hidden />
                    <div>
                      <h3 className="font-extrabold">{it.title}</h3>
                      <p className="mt-1 text-sm leading-6 text-muted-foreground">{it.text}</p>
                    </div>
                  </li>
                </Reveal>
              );
            })}
          </ul>
        </div>
        <Reveal delay={0.15}>
          <Tilt className="rounded-3xl">
            <div aria-hidden className={cn(glass, "relative flex flex-col gap-4 overflow-hidden p-5 sm:p-7")}>
              <div className="absolute -top-20 -end-20 size-60 rounded-full bg-violet-500/25 blur-3xl" />
              <div className="relative flex items-center gap-3">
                <div className="relative size-11">
                  <motion.div
                    className="absolute inset-0 rounded-full bg-linear-to-br from-indigo-500 via-violet-500 to-cyan-400"
                    animate={reduce ? undefined : { rotate: 360 }}
                    transition={{ duration: 8, repeat: Infinity, ease: "linear" }}
                  />
                  <div className="absolute inset-[3px] grid place-items-center rounded-full bg-card">
                    <BrainCircuit className="size-5 text-violet-500" />
                  </div>
                </div>
                <div className="h-2 w-24 rounded bg-foreground/15" />
              </div>
              <div className="relative ms-auto max-w-[85%] rounded-2xl rounded-se-sm bg-primary px-4 py-3 text-sm leading-7 text-primary-foreground">
                {a.chat.q}
              </div>
              <div className="relative max-w-[85%] rounded-2xl rounded-ss-sm border bg-background/60 px-4 py-3 text-sm leading-7">
                {a.chat.a}
                <div className="mt-3 flex gap-1.5">
                  {[0, 1, 2].map((k) => (
                    <motion.span
                      key={k}
                      className="size-1.5 rounded-full bg-cyan-500"
                      animate={reduce ? undefined : { opacity: [0.3, 1, 0.3] }}
                      transition={{ duration: 1.2, repeat: Infinity, delay: k * 0.2 }}
                    />
                  ))}
                </div>
              </div>
            </div>
          </Tilt>
        </Reveal>
      </div>
    </Block>
  );
}

/* ───────────────────────── security ───────────────────────── */

const SEC_ICONS = [UserRoundCog, Fingerprint, FileSearch, Lock];

function Security() {
  const s = LANDING.security;
  return (
    <Block id="security">
      <div className="grid grid-cols-1 items-center gap-12 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
        <Reveal className="order-last lg:order-first">
          <Shield3D />
        </Reveal>
        <div>
          <Heading eyebrow={s.eyebrow} title={s.title} lead={s.lead} />
          <ul className="mt-8 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {s.items.map((it, i) => {
              const Icon = SEC_ICONS[i];
              return (
                <Reveal key={it.title} delay={i * 0.07}>
                  <li className={cn(glass, "flex h-full gap-3 rounded-2xl p-4")}>
                    <Icon className="mt-1 size-5 shrink-0 text-cyan-600 dark:text-cyan-300" aria-hidden />
                    <div>
                      <h3 className="font-extrabold">{it.title}</h3>
                      <p className="mt-1 text-sm leading-6 text-muted-foreground">{it.text}</p>
                    </div>
                  </li>
                </Reveal>
              );
            })}
          </ul>
        </div>
      </div>
    </Block>
  );
}

/** An extruded shield, lit from the top left, floating over its shadow. */
function Shield3D() {
  const reduce = useReducedMotion();
  const face = "M100 18 L168 44 V104 C168 146 138 174 100 190 C62 174 32 146 32 104 V44 Z";
  return (
    <motion.svg
      viewBox="0 0 200 230"
      className="mx-auto w-full max-w-[280px] overflow-visible"
      aria-hidden
      animate={reduce ? undefined : { y: [0, -8, 0] }}
      transition={{ duration: 5, repeat: Infinity, ease: "easeInOut" }}
    >
      <defs>
        <linearGradient id="sh-face" x1="40" y1="20" x2="160" y2="190" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#a5b4fc" />
          <stop offset="0.5" stopColor="#6366f1" />
          <stop offset="1" stopColor="#7c3aed" />
        </linearGradient>
        <linearGradient id="sh-side" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#3730a3" />
          <stop offset="1" stopColor="#2e1065" />
        </linearGradient>
        <radialGradient id="sh-shadow">
          <stop offset="0" stopColor="#6366f1" stopOpacity={0.45} />
          <stop offset="1" stopColor="#6366f1" stopOpacity={0} />
        </radialGradient>
      </defs>
      <ellipse cx="100" cy="220" rx="70" ry="10" fill="url(#sh-shadow)" />
      {/* extrusion: the same outline stepped down and to the right */}
      {[10, 8, 6, 4, 2].map((k) => (
        <path key={k} d={face} transform={`translate(${k * 0.7} ${k})`} fill="url(#sh-side)" />
      ))}
      <path d={face} fill="url(#sh-face)" />
      <path d="M100 18 L168 44 V70 C130 60 70 60 32 70 V44 Z" fill="#ffffff" opacity={0.18} />
      <path d={face} fill="none" stroke="#ffffff" strokeOpacity={0.6} strokeWidth={1.5} />
      <path d="M72 104 L93 125 L132 84" fill="none" stroke="#22d3ee" strokeWidth={12} strokeLinecap="round" strokeLinejoin="round" />
      <path d="M72 101 L93 122 L132 81" fill="none" stroke="#ecfeff" strokeWidth={4} strokeLinecap="round" strokeLinejoin="round" />
    </motion.svg>
  );
}

/* ───────────────────────── contact ───────────────────────── */

function telegramHref(v: string) {
  return /^https?:\/\//.test(v) ? v : `https://t.me/${v.replace(/^@/, "")}`;
}

function Contact({ site }: { site: LandingSite }) {
  const c = LANDING.contact;
  const cards = [
    site.email && { icon: Mail, label: c.email, value: site.email, href: `mailto:${site.email}` },
    site.phone && { icon: Phone, label: c.phone, value: site.phone, href: `tel:${site.phone.replace(/[^\d+]/g, "")}` },
    site.telegram && { icon: Send, label: c.telegram, value: site.telegram, href: telegramHref(site.telegram) },
  ].filter(Boolean) as { icon: typeof Mail; label: string; value: string; href: string }[];
  return (
    <Block id="contact" className="pb-[18vh]">
      <Heading eyebrow={c.eyebrow} title={c.title} lead={c.lead} />
      {cards.length ? (
        <ul className="mt-12 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {cards.map((k, i) => (
            <Reveal key={k.label} delay={i * 0.08}>
              <li>
                <Tilt className="rounded-3xl">
                  <a
                    href={k.href}
                    target={k.href.startsWith("http") ? "_blank" : undefined}
                    rel={k.href.startsWith("http") ? "noopener noreferrer" : undefined}
                    className={cn(glass, "flex flex-col items-center gap-3 p-7 text-center outline-none focus-visible:ring-2 focus-visible:ring-ring")}
                  >
                    <IsoBadge icon={k.icon} className="scale-90" />
                    <span className="font-extrabold">{k.label}</span>
                    <span dir="ltr" className="max-w-full truncate text-sm text-muted-foreground">{k.value}</span>
                  </a>
                </Tilt>
              </li>
            </Reveal>
          ))}
        </ul>
      ) : (
        <Reveal>
          <p className={cn(glass, "mt-12 flex items-center gap-3 p-6 text-muted-foreground")}>
            <ShieldCheck className="size-5 text-primary" aria-hidden /> {c.empty}
          </p>
        </Reveal>
      )}
    </Block>
  );
}

/* ───────────────────────── footer ───────────────────────── */

function Footer({ brand, tagline, portalOpen, year }: { brand: string; tagline: string; portalOpen: boolean; year: string }) {
  const f = LANDING.footer;
  const link = "text-sm text-muted-foreground transition hover:text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring rounded";
  return (
    <footer className="relative z-10 border-t bg-background/80 px-4 pt-12 pb-8 backdrop-blur-xl">
      <div className="mx-auto flex max-w-7xl flex-col items-center gap-6 text-center">
        <LogoMark size={56} animated className="drop-shadow-[0_10px_24px_rgb(99_102_241/0.45)]" />
        <div>
          <div className="text-lg font-black">{brand}</div>
          <div className="text-xs text-muted-foreground">{tagline}</div>
        </div>
        <nav aria-label="پایین صفحه" className="flex flex-wrap justify-center gap-x-6 gap-y-2">
          {LANDING.nav.map((n) => (
            <a key={n.href} href={n.href} className={link}>{n.label}</a>
          ))}
          <Link href="/panel/login" className={link}>{f.panel}</Link>
          {portalOpen && <Link href="/portal" className={link}>{f.portal}</Link>}
        </nav>
        <p className="text-xs text-muted-foreground/80">{fill(f.rights, brand).replace("{year}", year)}</p>
      </div>
    </footer>
  );
}
