"use client";

// Two sections that pin themselves and turn the page's vertical scroll into a
// horizontal one: the work path (ad → lead → match → call → contract) and a
// tour of the panel. Both fall back to an ordinary vertical list when the
// visitor asks for reduced motion, and neither shows a real screenshot — the
// panels are stylised mock-ups, so no office's data is ever on the public page.

import { ArrowLeft, BadgeCheck, CalendarClock, FileSignature, PhoneCall, ScanLine, Sparkles, Users } from "lucide-react";
import { motion, useScroll, useTransform } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { cn } from "cn";
import { LANDING } from "@/content/landing";
import { faNum } from "@/lib/format";
import { usePrefersStill } from "@/lib/use-still";

const grad = "bg-linear-to-l from-indigo-600 via-violet-600 to-cyan-600 bg-clip-text text-transparent dark:from-indigo-300 dark:via-violet-300 dark:to-cyan-300";
const glass = "rounded-3xl border bg-card/70 backdrop-blur-xl shadow-[0_24px_60px_-30px_rgb(49_46_129/0.45)] dark:bg-white/[0.035] dark:shadow-[0_30px_80px_-30px_rgb(0_0_0/0.8)]";

/* ─────────────────── the pinned rail ─────────────────── */

/** A section as tall as its panels need, pinning one viewport-high strip and
 *  sliding the row inside it. In RTL the first panel sits at the right and the
 *  rest overflow to the left, so the row travels to the right (+x) as the page
 *  scrolls down. */
function Rail({
  id, eyebrow, title, lead, hint, count, children,
}: {
  id: string; eyebrow: string; title: readonly [string, string]; lead: string; hint: string;
  count: number; children: React.ReactNode;
}) {
  const outer = useRef<HTMLDivElement>(null);
  const view = useRef<HTMLDivElement>(null);
  const track = useRef<HTMLDivElement>(null);
  const stacked = usePrefersStill();
  const [distance, setDistance] = useState(0);

  // How far the row has to travel: everything of it that does not fit. While
  // stacked there is no track to measure, so the distance stays at nothing.
  useEffect(() => {
    const measure = () => {
      const t = track.current, v = view.current;
      if (t && v) setDistance(Math.max(0, t.scrollWidth - v.clientWidth));
    };
    measure();
    const ro = new ResizeObserver(measure);
    if (track.current) ro.observe(track.current);
    if (view.current) ro.observe(view.current);
    return () => ro.disconnect();
  }, [stacked]);

  const { scrollYProgress } = useScroll({ target: outer, offset: ["start start", "end end"] });
  const x = useTransform(scrollYProgress, [0, 1], [0, distance]);
  const bar = useTransform(scrollYProgress, [0, 1], ["0%", "100%"]);

  const head = (
    <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
      <div>
        <div className="flex items-center gap-3 text-xs font-bold tracking-[0.3em] text-violet-600 dark:text-violet-300">
          {eyebrow}
          <span aria-hidden className="h-px w-16 bg-linear-to-l from-transparent to-current" />
        </div>
        <h2 className="mt-3 text-[clamp(1.75rem,5vw,3.25rem)] leading-[1.2] font-black tracking-tight">
          {title[0]} <span className={grad}>{title[1]}</span>
        </h2>
      </div>
      <p className="max-w-md text-sm leading-7 text-muted-foreground">{lead}</p>
    </div>
  );

  // Reduced motion: no pin and no sideways travel, just the panels stacked.
  if (stacked) {
    return (
      <section id={id} className="mx-auto max-w-7xl scroll-mt-20 px-4 py-[14vh] sm:px-[6vw]">
        {head}
        <div className="mt-12 grid grid-cols-1 gap-6 md:grid-cols-2">{children}</div>
      </section>
    );
  }

  return (
    <section id={id} ref={outer} className="relative scroll-mt-0" style={{ height: `${count * 62 + 110}vh` }}>
      <div ref={view} className="sticky top-0 flex h-dvh flex-col justify-center overflow-hidden pt-24 pb-10">
        <div className="mx-auto w-full max-w-7xl px-4 sm:px-[6vw]">{head}</div>
        <div className="mt-8 min-h-0 flex-1" dir="rtl">
          <motion.div ref={track} className="flex h-full w-max items-stretch gap-5 px-4 sm:gap-7 sm:px-[6vw]" style={{ x }}>
            {children}
          </motion.div>
        </div>
        <div className="mx-auto mt-6 flex w-full max-w-7xl items-center gap-4 px-4 sm:px-[6vw]">
          <div className="h-1 flex-1 overflow-hidden rounded-full bg-foreground/10">
            <motion.div className="h-full rounded-full bg-linear-to-l from-indigo-500 via-violet-500 to-cyan-400" style={{ width: bar }} />
          </div>
          <span className="shrink-0 text-xs text-muted-foreground">{hint}</span>
        </div>
      </div>
    </section>
  );
}

/** One panel of a rail: a fixed-width card the row slides past. */
function Panel({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <article className={cn(glass, "relative flex h-full w-[82vw] shrink-0 flex-col overflow-hidden p-6 sm:w-[min(62vw,620px)] sm:p-8", "motion-reduce:w-auto", className)}>
      {children}
    </article>
  );
}

/* ─────────────────── the work path ─────────────────── */

const STATION_ART = {
  ad: AdArt, lead: LeadArt, match: MatchArt, call: CallArt, deal: DealArt,
} as const;

export function PathRail() {
  const p = LANDING.path;
  return (
    <Rail id="path" eyebrow={p.eyebrow} title={p.title} lead={p.lead} hint={p.hint} count={p.stations.length}>
      {p.stations.map((s, i) => {
        const Art = STATION_ART[s.key as keyof typeof STATION_ART];
        const last = i === p.stations.length - 1;
        return (
          <Panel key={s.key}>
            <div className="flex items-center justify-between gap-3">
              <span className="rounded-full border border-violet-500/40 bg-violet-500/10 px-3 py-1 text-[11px] font-bold text-violet-700 dark:text-violet-300">
                {s.tag}
              </span>
              <span className={cn("text-6xl leading-none font-black opacity-70 sm:text-7xl", grad)}>{faNum(i + 1)}</span>
            </div>
            <h3 className="mt-5 text-2xl font-black sm:text-3xl">{s.title}</h3>
            <p className="mt-3 text-sm leading-7 text-muted-foreground">{s.text}</p>
            <ul className="mt-4 flex flex-col gap-2">
              {s.bullets.map((b) => (
                <li key={b} className="flex items-start gap-2 text-sm text-muted-foreground">
                  <BadgeCheck className="mt-0.5 size-4 shrink-0 text-cyan-600 dark:text-cyan-300" aria-hidden />
                  {b}
                </li>
              ))}
            </ul>
            <div className="mt-auto pt-6">
              <Art />
            </div>
            {!last && (
              <ArrowLeft aria-hidden className="absolute bottom-6 left-6 size-5 animate-pulse text-muted-foreground/60" />
            )}
          </Panel>
        );
      })}
    </Rail>
  );
}

/* Each station's picture. Isometric or near-isometric, lit from the top-left,
   built from the same violet → cyan ramp as the rest of the page. */

const ISO = "translate(0 8) matrix(0.866 0.5 -0.866 0.5 0 0)"; // 30° isometric

function Frame({ children, label }: { children: React.ReactNode; label?: string }) {
  return (
    <div aria-hidden className="relative overflow-hidden rounded-2xl border bg-background/40 p-4">
      {label && <span className="absolute top-2 end-3 text-[10px] font-bold text-muted-foreground">{label}</span>}
      {children}
    </div>
  );
}

const line = (w: string, dim = false) => (
  <u className={cn("block h-1.5 rounded", dim ? "bg-foreground/10" : "bg-foreground/20")} style={{ width: w }} />
);

/** An ad card with a scanning line sweeping down it. */
function AdArt() {
  return (
    <Frame label="دیوار">
      <div className="flex gap-3">
        <span className="size-16 shrink-0 rounded-xl bg-linear-to-br from-violet-400/50 to-cyan-300/30" />
        <div className="flex flex-1 flex-col gap-2 pt-1">
          {line("70%")}
          {line("45%", true)}
          <div className="mt-1 flex gap-1.5">
            {["۲ خواب", "۹۵ متر", "پارکینگ"].map((t) => (
              <span key={t} className="rounded-full border bg-background/60 px-2 py-0.5 text-[9px] text-muted-foreground">{t}</span>
            ))}
          </div>
        </div>
      </div>
      <motion.div
        className="pointer-events-none absolute inset-x-0 h-14 bg-linear-to-b from-transparent via-cyan-400/25 to-transparent"
        animate={{ y: [-20, 96, -20] }}
        transition={{ duration: 3.4, repeat: Infinity, ease: "easeInOut" }}
      />
      <motion.div
        className="pointer-events-none absolute inset-x-0 h-px bg-cyan-400/80 shadow-[0_0_12px_2px_rgb(34_211_238/0.6)]"
        animate={{ y: [6, 110, 6] }}
        transition={{ duration: 3.4, repeat: Infinity, ease: "easeInOut" }}
      />
      <ScanLine aria-hidden className="absolute bottom-2 start-3 size-4 text-cyan-500/70" />
    </Frame>
  );
}

/** The same ad, now a file with an owner and a state. */
function LeadArt() {
  return (
    <Frame label="پرونده">
      <div className="flex flex-col gap-2">
        {[
          { s: "تماس گرفته شد", tone: "text-cyan-700 dark:text-cyan-300 border-cyan-500/40 bg-cyan-500/10" },
          { s: "بازدید فردا", tone: "text-violet-700 dark:text-violet-300 border-violet-500/40 bg-violet-500/10" },
          { s: "در انتظار مالک", tone: "text-amber-700 dark:text-amber-300 border-amber-500/40 bg-amber-500/10" },
        ].map((r, i) => (
          <motion.div
            key={r.s}
            className="flex items-center gap-2 rounded-xl border bg-background/50 p-2"
            animate={{ x: [0, i === 0 ? -5 : 0, 0] }}
            transition={{ duration: 3, repeat: Infinity, delay: i * 0.5, ease: "easeInOut" }}
          >
            <span className="grid size-6 shrink-0 place-items-center rounded-full bg-linear-to-br from-indigo-500 to-violet-600 text-[9px] font-black text-white">
              {["م", "ز", "ک"][i]}
            </span>
            <span className="flex flex-1 flex-col gap-1">{line("80%")}{line("40%", true)}</span>
            <span className={cn("shrink-0 rounded-full border px-2 py-0.5 text-[9px] font-bold", r.tone)}>{r.s}</span>
          </motion.div>
        ))}
      </div>
    </Frame>
  );
}

/** Ads on one side, customers on the other, with a score in between. */
function MatchArt() {
  return (
    <Frame label="تطبیق">
      <div className="relative grid grid-cols-[1fr_auto_1fr] items-center gap-2">
        <div className="flex flex-col gap-1.5">
          {[0, 1, 2].map((i) => <span key={i} className="h-5 rounded-lg border bg-background/60" />)}
        </div>
        <motion.div
          className="grid size-12 place-items-center rounded-full bg-linear-to-br from-indigo-500 via-violet-500 to-cyan-400 text-[11px] font-black text-white shadow-[0_0_24px_-4px_rgb(139_92_246/0.9)]"
          animate={{ scale: [1, 1.09, 1] }}
          transition={{ duration: 2.2, repeat: Infinity, ease: "easeInOut" }}
        >
          ۹۲٪
        </motion.div>
        <div className="flex flex-col gap-1.5">
          {[0, 1, 2].map((i) => (
            <span key={i} className="flex h-5 items-center gap-1.5 rounded-lg border bg-background/60 px-1.5">
              <Users className="size-2.5 text-violet-500" aria-hidden />
              {line("60%", true)}
            </span>
          ))}
        </div>
      </div>
      <Sparkles aria-hidden className="absolute bottom-2 start-3 size-4 text-violet-500/70" />
    </Frame>
  );
}

/** Today's call queue on an isometric phone. */
function CallArt() {
  return (
    <Frame label="صف تماس">
      <div className="flex items-center gap-4">
        <svg viewBox="0 0 90 110" className="h-24 w-auto shrink-0 overflow-visible">
          <defs>
            <linearGradient id="ph-face" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stopColor="#a5b4fc" /><stop offset="1" stopColor="#7c3aed" />
            </linearGradient>
          </defs>
          <g transform={ISO}>
            <rect x="8" y="8" width="52" height="86" rx="10" fill="#312e81" opacity="0.55" />
            <rect x="4" y="2" width="52" height="86" rx="10" fill="url(#ph-face)" />
            <rect x="10" y="10" width="40" height="70" rx="6" fill="#0b0b18" opacity="0.65" />
            {[0, 1, 2, 3].map((i) => (
              <motion.rect
                key={i} x="15" y={18 + i * 15} width="30" height="9" rx="3" fill="#a5b4fc"
                animate={{ opacity: [0.35, 0.9, 0.35] }}
                transition={{ duration: 2.4, repeat: Infinity, delay: i * 0.35, ease: "easeInOut" }}
              />
            ))}
          </g>
          <motion.g animate={{ scale: [1, 1.14, 1] }} transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }} style={{ originX: "70px", originY: "22px" }}>
            <circle cx="70" cy="22" r="13" fill="#22d3ee" opacity="0.2" />
            <circle cx="70" cy="22" r="8" fill="#06b6d4" />
          </motion.g>
        </svg>
        <ul className="flex flex-1 flex-col gap-1.5">
          {["۰۹:۳۰", "۱۱:۰۰", "۱۴:۱۵"].map((t, i) => (
            <li key={t} className="flex items-center gap-2 rounded-lg border bg-background/50 px-2 py-1">
              <PhoneCall className="size-3 shrink-0 text-cyan-600 dark:text-cyan-300" aria-hidden />
              <span className="flex flex-1 flex-col gap-1">{line("75%")}</span>
              <span className="shrink-0 text-[9px] text-muted-foreground tabular-nums">{t}</span>
              {i === 0 && <CalendarClock className="size-3 shrink-0 text-violet-500" aria-hidden />}
            </li>
          ))}
        </ul>
      </div>
    </Frame>
  );
}

/** The deal's stages, the last one signing itself. */
function DealArt() {
  const stages = ["مذاکره", "پیش‌قرارداد", "امضا"];
  return (
    <Frame label="معامله">
      <div className="flex items-center gap-1.5">
        {stages.map((s, i) => (
          <div key={s} className="flex min-w-0 flex-1 items-center gap-1.5">
            <motion.div
              className={cn(
                "min-w-0 flex-1 truncate rounded-lg border px-2 py-1.5 text-center text-[10px] font-bold",
                i === stages.length - 1
                  ? "border-transparent bg-linear-to-l from-indigo-500 to-cyan-500 text-white"
                  : "bg-background/60 text-muted-foreground",
              )}
              animate={i === stages.length - 1 ? { y: [0, -3, 0] } : undefined}
              transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
            >
              {s}
            </motion.div>
            {i < stages.length - 1 && <ArrowLeft className="size-3 shrink-0 text-muted-foreground/60" aria-hidden />}
          </div>
        ))}
      </div>
      <div className="mt-3 flex items-end gap-3 rounded-xl border bg-background/50 p-3">
        <FileSignature className="size-6 shrink-0 text-violet-600 dark:text-violet-300" aria-hidden />
        <svg viewBox="0 0 120 30" className="h-7 flex-1">
          <motion.path
            d="M4 22 C 16 4, 24 28, 36 14 S 56 2, 66 18 S 86 26, 116 8"
            fill="none" stroke="#22d3ee" strokeWidth="2.5" strokeLinecap="round"
            initial={{ pathLength: 0 }}
            animate={{ pathLength: [0, 1, 1, 0] }}
            transition={{ duration: 4.5, times: [0, 0.5, 0.8, 1], repeat: Infinity, ease: "easeInOut" }}
          />
        </svg>
      </div>
    </Frame>
  );
}

/* ─────────────────── the panel tour ─────────────────── */

const STOP_ART: Record<string, () => React.ReactElement> = {
  dashboard: TourDashboard, scraper: TourScraper, crm: TourCrm,
  customers: TourCustomers, calls: TourCalls, reports: TourReports,
};

export function TourRail({ domain }: { domain: string }) {
  const t = LANDING.tour;
  return (
    <Rail id="tour" eyebrow={t.eyebrow} title={t.title} lead={t.lead} hint={t.hint} count={t.stops.length}>
      {t.stops.map((s, i) => {
        const Art = STOP_ART[s.key];
        return (
          <Panel key={s.key}>
            <div className="mb-4 flex items-center gap-1.5" dir="ltr" aria-hidden>
              <i className="size-2.5 rounded-full bg-fuchsia-400" />
              <i className="size-2.5 rounded-full bg-violet-400" />
              <i className="size-2.5 rounded-full bg-cyan-400" />
              <span className="ms-2 h-5 flex-1 truncate rounded-full border bg-background/40 px-3 text-[10px] leading-5 text-muted-foreground">
                {domain}/panel/{s.key}
              </span>
            </div>
            <div className="min-h-0 flex-1">
              <Art />
            </div>
            <h3 className="mt-5 flex items-baseline gap-3 text-2xl font-black">
              <span className={cn("text-base opacity-70", grad)}>{faNum(i + 1, { minimumIntegerDigits: 2 })}</span>
              {s.title}
            </h3>
            <p className="mt-2 text-sm leading-7 text-muted-foreground">{s.text}</p>
          </Panel>
        );
      })}
    </Rail>
  );
}

const TOUR_BOX = "h-full rounded-2xl border bg-background/40 p-3 sm:p-4";

function TourDashboard() {
  const bars = [42, 66, 51, 88, 60, 95, 73];
  return (
    <div aria-hidden className={cn(TOUR_BOX, "flex flex-col gap-3")}>
      <div className="grid grid-cols-3 gap-2">
        {["آگهی امروز", "لید باز", "تماس مانده"].map((l) => (
          <div key={l} className="rounded-xl border bg-background/50 p-2">
            <b className={cn("block text-base font-black", grad)}>—</b>
            <span className="text-[9px] text-muted-foreground">{l}</span>
          </div>
        ))}
      </div>
      <div className="flex min-h-0 flex-1 items-end gap-1.5 rounded-xl border bg-background/30 p-3">
        {bars.map((h, i) => (
          <motion.i
            key={i}
            className="flex-1 origin-bottom rounded-t-md bg-linear-to-t from-cyan-400/50 via-violet-500 to-fuchsia-400"
            style={{ height: `${h}%` }}
            animate={{ scaleY: [0.85, 1.05, 0.85] }}
            transition={{ duration: 2.8, repeat: Infinity, delay: i * 0.18, ease: "easeInOut" }}
          />
        ))}
      </div>
    </div>
  );
}

function TourScraper() {
  return (
    <div aria-hidden className={cn(TOUR_BOX, "flex flex-col gap-3")}>
      <div className="flex flex-wrap gap-1.5">
        {["تهران", "آپارتمان", "۵ تا ۸ میلیارد", "۲ خواب", "آسانسور", "هفتهٔ گذشته"].map((c) => (
          <span key={c} className="rounded-full border border-violet-500/40 bg-violet-500/10 px-2 py-0.5 text-[10px] font-bold text-violet-700 dark:text-violet-300">
            {c}
          </span>
        ))}
      </div>
      <div className="flex min-h-0 flex-1 flex-col justify-center gap-2 rounded-xl border bg-background/30 p-3">
        <div className="flex justify-between text-[10px] text-muted-foreground"><span>در حال اسکرپ</span><span className="tabular-nums">۶۸٪</span></div>
        <div className="h-2 overflow-hidden rounded-full bg-foreground/10">
          <motion.div
            className="h-full rounded-full bg-linear-to-l from-indigo-500 via-violet-500 to-cyan-400"
            animate={{ width: ["12%", "68%", "12%"] }}
            transition={{ duration: 4.5, repeat: Infinity, ease: "easeInOut" }}
          />
        </div>
        <div className="mt-1 flex gap-3 text-[10px] text-muted-foreground">
          <span>صفحه ۱۲</span><span>آگهی تازه ۳۴</span><span>تکراری ۹</span>
        </div>
      </div>
    </div>
  );
}

function TourCrm() {
  return (
    <div aria-hidden className={cn(TOUR_BOX, "flex flex-col gap-1.5")}>
      {[0, 1, 2, 3, 4].map((i) => (
        <motion.div
          key={i}
          className="flex items-center gap-2 rounded-lg border bg-background/50 px-2 py-1.5"
          animate={{ opacity: [0.65, 1, 0.65] }}
          transition={{ duration: 3.2, repeat: Infinity, delay: i * 0.22, ease: "easeInOut" }}
        >
          <span className="size-5 shrink-0 rounded-md bg-linear-to-br from-violet-400/60 to-cyan-300/40" />
          <span className="flex flex-1 flex-col gap-1">{line("70%")}{line("38%", true)}</span>
          <span className="shrink-0 rounded-full border border-cyan-500/40 bg-cyan-500/10 px-1.5 py-0.5 text-[8px] font-bold text-cyan-700 dark:text-cyan-300">
            {["جدید", "تماس", "بازدید", "مذاکره", "بسته"][i]}
          </span>
        </motion.div>
      ))}
    </div>
  );
}

function TourCustomers() {
  return (
    <div aria-hidden className={cn(TOUR_BOX, "flex flex-col gap-2")}>
      <div className="flex items-center gap-2">
        <span className="grid size-9 place-items-center rounded-full bg-linear-to-br from-indigo-500 to-violet-600 text-xs font-black text-white">ر</span>
        <span className="flex flex-1 flex-col gap-1">{line("55%")}{line("30%", true)}</span>
      </div>
      {[["بودجه", "۷ تا ۸ میلیارد"], ["منطقه", "شمال شهر"], ["متراژ", "۹۰ تا ۱۲۰ متر"]].map(([k, v]) => (
        <div key={k} className="flex items-center justify-between rounded-lg border bg-background/50 px-2 py-1.5 text-[10px]">
          <span className="text-muted-foreground">{k}</span>
          <span className="font-bold">{v}</span>
        </div>
      ))}
      <motion.div
        className="mt-auto rounded-lg border border-cyan-500/40 bg-cyan-500/10 px-2 py-1.5 text-center text-[10px] font-bold text-cyan-700 dark:text-cyan-300"
        animate={{ opacity: [0.7, 1, 0.7] }}
        transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
      >
        ۴ ملک با این نیاز می‌خواند
      </motion.div>
    </div>
  );
}

function TourCalls() {
  return (
    <div aria-hidden className={cn(TOUR_BOX, "flex flex-col gap-1.5")}>
      {["۰۹:۳۰", "۱۰:۱۵", "۱۱:۰۰", "۱۳:۴۵", "۱۵:۲۰"].map((t, i) => (
        <div key={t} className={cn("flex items-center gap-2 rounded-lg border px-2 py-1.5", i === 0 ? "border-cyan-500/50 bg-cyan-500/10" : "bg-background/50")}>
          <motion.span
            className={cn("grid size-5 shrink-0 place-items-center rounded-full", i === 0 ? "bg-cyan-500 text-white" : "bg-foreground/10")}
            animate={i === 0 ? { scale: [1, 1.15, 1] } : undefined}
            transition={{ duration: 1.5, repeat: Infinity, ease: "easeInOut" }}
          >
            <PhoneCall className="size-2.5" aria-hidden />
          </motion.span>
          <span className="flex flex-1 flex-col gap-1">{line(i === 0 ? "80%" : "62%", i !== 0)}</span>
          <span className="shrink-0 text-[9px] text-muted-foreground tabular-nums">{t}</span>
        </div>
      ))}
    </div>
  );
}

// One ring per metric, each starting where the last ended. Offsets are worked
// out here, not while rendering, so nothing is reassigned mid-render.
const DONUT_R = 34;
const DONUT_C = 2 * Math.PI * DONUT_R;
const DONUT = [
  { a: 0.42, c: "#6366f1" }, { a: 0.27, c: "#a78bfa" }, { a: 0.19, c: "#22d3ee" }, { a: 0.12, c: "#f472b6" },
].map((s, i, all) => ({ ...s, off: all.slice(0, i).reduce((t, p) => t + p.a, 0) }));

function TourReports() {
  return (
    <div aria-hidden className={cn(TOUR_BOX, "flex items-center gap-4")}>
      <svg viewBox="0 0 100 100" className="h-28 w-28 shrink-0 overflow-visible">
        {/* the same ring stepped down twice, so the donut reads as a solid disc */}
        {[4, 2, 0].map((dy) => (
          <g key={dy} transform={`translate(0 ${dy})`} opacity={dy ? 0.45 : 1}>
            {DONUT.map((s, i) => (
              <motion.circle
                key={i} cx="50" cy="50" r={DONUT_R} fill="none" stroke={s.c} strokeWidth="15"
                strokeDasharray={`${DONUT_C * s.a - 2} ${DONUT_C}`}
                strokeDashoffset={-DONUT_C * s.off}
                transform="rotate(-90 50 50)"
                animate={{ opacity: dy ? [0.35, 0.5, 0.35] : [0.85, 1, 0.85] }}
                transition={{ duration: 3, repeat: Infinity, delay: i * 0.25, ease: "easeInOut" }}
              />
            ))}
          </g>
        ))}
      </svg>
      <div className="flex flex-1 flex-col gap-2">
        {["تماس", "بازدید", "معامله", "امتیاز روز"].map((l, i) => (
          <div key={l} className="flex items-center gap-2">
            <i className="size-2 shrink-0 rounded-full" style={{ background: DONUT[i].c }} />
            <span className="flex-1 text-[10px] text-muted-foreground">{l}</span>
            <span className="h-1.5 w-16 overflow-hidden rounded-full bg-foreground/10">
              <motion.i
                className="block h-full rounded-full"
                style={{ background: DONUT[i].c }}
                animate={{ width: [`${DONUT[i].a * 120}%`, `${DONUT[i].a * 200}%`, `${DONUT[i].a * 120}%`] }}
                transition={{ duration: 3.4, repeat: Infinity, delay: i * 0.2, ease: "easeInOut" }}
              />
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
