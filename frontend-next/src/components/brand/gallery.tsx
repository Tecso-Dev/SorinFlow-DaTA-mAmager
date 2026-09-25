"use client";

// /brand-preview: the three logo concepts side by side, in every place a logo
// has to survive (a 16px favicon, a maskable app icon, a browser tab, a phone
// home screen), so the owner can pick one. Not indexed, not linked.

import { Camera, Map, MessageCircle, Music, Phone, Settings, X } from "lucide-react";
import { cn } from "cn";
import { Reveal } from "@/components/viz";
import { CURRENT_CONCEPT } from "./logo";
import { CONCEPTS, type ConceptKey, type MarkProps } from "./logos";

type Site = { brandName: string; brandNameLatin: string; tagline: string; domain: string };

const ORDER: { key: ConceptKey; letter: string; name: string; blurb: string }[] = [
  {
    key: "house",
    letter: "الف",
    name: "خانهٔ S",
    blurb: "خانه‌ای ایزومتریک که دیوار روشنش یک S برجسته دارد: ملک، و جریان کاری که از آن می‌گذرد.",
  },
  {
    key: "ribbon",
    letter: "ب",
    name: "روبان تاشده",
    blurb: "نواری که چهار بار تا می‌خورد؛ دو تای اول بام می‌سازند و دو تای بعد حرف S را تمام می‌کنند.",
  },
  {
    key: "towers",
    letter: "ج",
    name: "برج‌ها و مدار",
    blurb: "سه برج که هر کدام از قبلی بلندتر است و حلقه‌ای از داده که دورشان می‌چرخد.",
  },
];

const SIZES = [16, 32, 64, 128, 256];
// The two backgrounds every mark must work on: the dark and light page colours.
const DARK = "bg-[#05050a] text-[#eef1f8]";
const LIGHT = "bg-[#f5f6fb] text-[#0e1022]";

export function BrandGallery({ site }: { site: Site }) {
  return (
    <main className="mx-auto flex max-w-6xl flex-col gap-10 px-4 py-10 sm:px-6">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-black tracking-tight sm:text-3xl">سه پیشنهاد برای لوگوی {site.brandName}</h1>
        <p className="text-sm text-muted-foreground">
          هر کدام در اندازهٔ ۱۶ تا ۲۵۶ پیکسل، روی زمینهٔ تیره و روشن، به‌شکل آیکون برنامه و در زبانهٔ مرورگر.
          فعلاً گزینهٔ «{ORDER.find((o) => o.key === CURRENT_CONCEPT)?.letter}» در سایت استفاده می‌شود.
        </p>
      </header>
      {ORDER.map((c) => (
        <Concept key={c.key} concept={c} site={site} />
      ))}
    </main>
  );
}

function Concept({ concept, site }: { concept: (typeof ORDER)[number]; site: Site }) {
  const { Mark, Lockup } = CONCEPTS[concept.key];
  const current = concept.key === CURRENT_CONCEPT;
  return (
    <Reveal>
      <section
        aria-labelledby={`c-${concept.key}`}
        className="flex flex-col gap-5 rounded-3xl border bg-card p-4 sm:p-6 dark:bg-linear-to-b dark:from-white/[0.035] dark:to-white/[0.008]"
      >
        <div className="flex flex-wrap items-center gap-3">
          <span className="grid size-10 place-items-center rounded-xl bg-primary text-lg font-black text-primary-foreground">
            {concept.letter}
          </span>
          <div className="min-w-0 flex-1">
            <h2 id={`c-${concept.key}`} className="text-xl font-black">
              {concept.letter} — {concept.name}
              {current && (
                <span className="ms-2 rounded-full bg-success/15 px-2 py-0.5 align-middle text-xs font-bold text-success">
                  در حال استفاده
                </span>
              )}
            </h2>
            <p className="text-sm text-muted-foreground">{concept.blurb}</p>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-3">
          {[DARK, LIGHT].map((bg) => (
            <div key={bg} dir="ltr" className={cn("overflow-x-auto rounded-2xl p-4", bg)}>
              <div className="flex min-w-max items-end gap-5">
                {SIZES.map((s) => (
                  <figure key={s} className="flex flex-col items-center gap-1.5">
                    <Mark size={s} />
                    <figcaption className="text-[10px] opacity-60">{s}px</figcaption>
                  </figure>
                ))}
              </div>
            </div>
          ))}
        </div>

        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
          <Tile title="لوگو با نام، تیره" className={DARK}>
            <Lockup name={site.brandName} tagline={site.tagline} size={48} nameClassName="text-xl" taglineClassName="text-[#8f9ab0]" />
          </Tile>
          <Tile title="لوگو با نام، روشن" className={LIGHT}>
            <Lockup name={site.brandName} tagline={site.tagline} size={48} nameClassName="text-xl" taglineClassName="text-[#5a6378]" />
          </Tile>
          <Tile title="متحرک" className={DARK}>
            <Mark size={112} animated />
          </Tile>
          <Tile title="آیکون قابل‌برش (منطقهٔ امن ۸۰٪)" className={LIGHT}>
            <div className="flex items-center gap-4">
              <Maskable Mark={Mark} size={96} guide />
              <Maskable Mark={Mark} size={96} round />
            </div>
          </Tile>
        </div>

        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
          <div className="flex flex-col gap-3">
            <BrowserTab Mark={Mark} site={site} dark />
            <BrowserTab Mark={Mark} site={site} />
          </div>
          <PhoneHome Mark={Mark} name={site.brandName} />
        </div>
      </section>
    </Reveal>
  );
}

function Tile({ title, className, children }: { title: string; className: string; children: React.ReactNode }) {
  return (
    <figure className={cn("flex min-h-40 flex-col items-center justify-center gap-3 rounded-2xl p-4", className)}>
      {children}
      <figcaption className="text-xs opacity-60">{title}</figcaption>
    </figure>
  );
}

type MarkC = (p: MarkProps) => React.ReactNode;

/** An app icon as Android masks it: the art must stay inside the middle 80%. */
function Maskable({ Mark, size, guide, round }: { Mark: MarkC; size: number; guide?: boolean; round?: boolean }) {
  return (
    <div
      className={cn(
        "relative grid place-items-center overflow-hidden bg-[radial-gradient(circle_at_30%_25%,#1c1b3a,#05050a_70%)]",
        round ? "rounded-full" : "rounded-[22%]",
      )}
      style={{ width: size, height: size }}
    >
      <Mark size={Math.round(size * 0.62)} />
      {guide && (
        <div aria-hidden className="absolute inset-[10%] rounded-full border border-dashed border-cyan-300/70" />
      )}
    </div>
  );
}

function BrowserTab({ Mark, site, dark }: { Mark: MarkC; site: Site; dark?: boolean }) {
  return (
    <div className={cn("overflow-hidden rounded-2xl border", dark ? "border-white/10 bg-[#1b1b24]" : "border-black/10 bg-[#dfe3ee]")}>
      <div className="flex items-end gap-1 px-3 pt-2" dir="ltr">
        <div
          className={cn(
            "flex w-60 max-w-full min-w-0 items-center gap-2 rounded-t-xl px-3 py-2 text-xs",
            dark ? "bg-[#2a2a36] text-white" : "bg-white text-[#0e1022]",
          )}
        >
          <Mark size={16} />
          <span className="min-w-0 flex-1 truncate" dir="rtl">
            {site.brandName} — {site.tagline}
          </span>
          <X className="size-3 opacity-60" aria-hidden />
        </div>
        <div className={cn("mb-2 h-4 w-24 rounded-md", dark ? "bg-white/5" : "bg-black/5")} />
      </div>
      <div className={cn("flex items-center gap-2 px-3 py-2", dark ? "bg-[#2a2a36]" : "bg-white")} dir="ltr">
        <div className={cn("flex-1 truncate rounded-full px-3 py-1 text-xs", dark ? "bg-[#1b1b24] text-white/70" : "bg-[#eef0f7] text-black/60")}>
          {site.domain}
        </div>
      </div>
    </div>
  );
}

const APPS = [
  { icon: Phone, bg: "bg-emerald-500" },
  { icon: MessageCircle, bg: "bg-sky-500" },
  { icon: Camera, bg: "bg-zinc-600" },
  { icon: Map, bg: "bg-amber-500" },
  { icon: Music, bg: "bg-rose-500" },
  { icon: Settings, bg: "bg-slate-500" },
];

function PhoneHome({ Mark, name }: { Mark: MarkC; name: string }) {
  return (
    <div className="mx-auto w-full max-w-[280px] rounded-[40px] border-4 border-[#1b1b24] bg-[linear-gradient(160deg,#312e81,#0b0b1a_55%,#164e63)] p-4 shadow-xl">
      <div className="mx-auto mb-5 h-5 w-24 rounded-full bg-black/70" />
      <div className="grid grid-cols-4 gap-x-3 gap-y-4">
        {APPS.slice(0, 3).map((a, i) => (
          <AppIcon key={i} label="" bg={a.bg}>
            <a.icon className="size-6 text-white" aria-hidden />
          </AppIcon>
        ))}
        <AppIcon label={name} bg="bg-[radial-gradient(circle_at_30%_25%,#1c1b3a,#05050a_70%)]">
          <Mark size={36} />
        </AppIcon>
        {APPS.slice(3).map((a, i) => (
          <AppIcon key={i} label="" bg={a.bg}>
            <a.icon className="size-6 text-white" aria-hidden />
          </AppIcon>
        ))}
      </div>
      <div className="mt-24 grid grid-cols-4 gap-3 rounded-3xl bg-white/10 p-2.5">
        {APPS.slice(0, 4).map((a, i) => (
          <div key={i} className={cn("grid aspect-square place-items-center rounded-[22%]", a.bg)}>
            <a.icon className="size-5 text-white" aria-hidden />
          </div>
        ))}
      </div>
    </div>
  );
}

function AppIcon({ label, bg, children }: { label: string; bg: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-1">
      <div className={cn("grid aspect-square w-full place-items-center rounded-[22%] shadow-md", bg)}>{children}</div>
      <span className="h-3.5 max-w-full truncate text-[10px] text-white">{label}</span>
    </div>
  );
}
