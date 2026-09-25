"use client";

import {
  ArrowUpLeft, Bot, Building2, CalendarDays, Camera, Check, CircleDashed, Clock, DatabaseBackup, Flame,
  Handshake, KeyRound, MessageCircle, PenLine, Phone, PhoneCall, PhoneMissed, Plus, Radar, Sparkles,
  Target, TrendingDown, TrendingUp, Users, UsersRound,
} from "lucide-react";
import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts";
import { cn } from "cn";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { Progress } from "@/components/ui/progress";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { faDate, faNum, faPercent, toman } from "@/lib/format";
import {
  activity, agenda, callGrid, calls, dealsByMonth, districts, funnel, kpis, leadSources, matches, me, spark,
  system, targets, team, teamRadar, trend, type CallItem, type KpiKey,
} from "./data";
import type { Variant } from "./shell";
import { CallHeatmap, CountUp, DealsByMonth, Donut3D, Reveal, Skyline, TargetGauge, TeamRadar, Tilt } from "./viz";

/* ───────────────────────── building blocks ───────────────────────── */

function Panel({
  title, hint, action, className, bodyClassName, children,
}: {
  title?: string; hint?: string; action?: React.ReactNode; className?: string; bodyClassName?: string;
  children: React.ReactNode;
}) {
  return (
    <section
      className={cn(
        "flex min-w-0 flex-col rounded-2xl border bg-card text-card-foreground",
        "va:shadow-sm va:dark:bg-linear-to-b va:dark:from-white/[0.035] va:dark:to-white/[0.008] va:dark:shadow-none",
        "vb:rounded-lg vb:shadow-none",
        "vc:rounded-[1.75rem] vc:border-transparent vc:shadow-[0_1px_2px_rgb(60_40_10/0.05),0_12px_32px_-18px_rgb(60_40_10/0.25)] vc:dark:border-border vc:dark:shadow-none",
        className,
      )}
    >
      {title && (
        <header className="flex items-start justify-between gap-3 px-5 pt-4 vb:px-4 vb:pt-3 vc:px-6 vc:pt-5">
          <div className="min-w-0">
            <h2 className="text-[15px] font-bold vb:text-sm vb:font-semibold vc:text-base">{title}</h2>
            {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
          </div>
          {action}
        </header>
      )}
      <div className={cn("flex-1 p-5 pt-3 vb:p-4 vb:pt-2.5 vc:p-6 vc:pt-4", bodyClassName)}>{children}</div>
    </section>
  );
}

function MoreLink({ children = "همه" }: { children?: React.ReactNode }) {
  return (
    <Button variant="ghost" size="sm" className="h-7 gap-1 px-2 text-xs text-muted-foreground hover:text-foreground">
      {children}
      <ArrowUpLeft className="size-3.5" />
    </Button>
  );
}

function Delta({ value, unit = "٪" }: { value: number; unit?: string }) {
  const up = value >= 0;
  const Icon = up ? TrendingUp : TrendingDown;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[11px] font-semibold tabular",
        up ? "bg-success/12 text-success" : "bg-destructive/12 text-destructive",
        "vb:rounded vb:bg-transparent vb:px-0",
      )}
    >
      {/* charts run right-to-left here, so a rising arrow points up-left */}
      <Icon className="size-3 -scale-x-100" />
      {faNum(Math.abs(value), { maximumFractionDigits: 1 })}
      {unit}
    </span>
  );
}

const initials = (name: string) => name.replace(/^(خانوادهٔ|آقای|خانم)\s+/, "").slice(0, 1);

/* ───────────────────────── header ───────────────────────── */

function Greeting({ variant }: { variant: Variant }) {
  const now = new Date();
  const date = faDate(now, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  const first = me.name.split(" ")[0];
  const actions = (
    <div className="flex flex-wrap gap-2">
      <Button className="gap-1.5 va:shadow-[0_8px_24px_-8px_rgb(99_102_241/0.8)] vc:rounded-full vb:h-8">
        <Plus className="size-4" /> لید تازه
      </Button>
      <Button variant="outline" className="gap-1.5 vc:rounded-full vb:h-8">
        <Users className="size-4" /> مشتری تازه
      </Button>
      <Button variant="outline" className="gap-1.5 vc:rounded-full vb:h-8">
        <Bot className="size-4" /> اسکرپ تازه
      </Button>
    </div>
  );

  if (variant === "c") {
    return (
      <section className="relative overflow-hidden rounded-[2rem] bg-linear-to-l from-teal-700 via-teal-600 to-cyan-700 p-6 text-white sm:p-8 dark:from-teal-900 dark:via-teal-800 dark:to-cyan-900">
        <TilePattern />
        <div
          aria-hidden
          className="pointer-events-none absolute -bottom-3 end-4 hidden w-[300px] opacity-90 xl:block [--border:rgb(255_255_255/0.25)] [--foreground:white] [--muted:rgb(255_255_255/0.10)] [--viz-high:#fde68a] [--viz-low:#ccfbf1]"
        >
          <Skyline data={districts} compact />
        </div>
        <div className="relative flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between xl:pe-[320px]">
          <div>
            <p className="text-sm text-white/75">{date}</p>
            <h1 className="mt-1 text-2xl font-black sm:text-3xl">روز بخیر، {first} 👋</h1>
            <p className="mt-2 max-w-xl text-sm leading-7 text-white/85">
              امروز {faNum(7)} تماس مانده، {faNum(2)} بازدید و یک امضای قرارداد دارید. موتور تطبیق هم{" "}
              {faNum(matches.length)} پیشنهاد تازه برای مشتری‌هایتان پیدا کرده است.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button className="gap-1.5 rounded-full bg-white text-teal-800 hover:bg-white/90">
              <Plus className="size-4" /> لید تازه
            </Button>
            <Button className="gap-1.5 rounded-full border border-white/30 bg-white/10 text-white hover:bg-white/20">
              <Users className="size-4" /> مشتری تازه
            </Button>
            <Button className="gap-1.5 rounded-full border border-white/30 bg-white/10 text-white hover:bg-white/20">
              <Bot className="size-4" /> اسکرپ تازه
            </Button>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
      <div>
        <p className="text-sm text-muted-foreground">{date}</p>
        <h1 className="mt-1 text-2xl font-black tracking-tight vb:text-xl vb:font-bold">
          روز بخیر، {first}
          <span className="va:bg-linear-to-l va:from-indigo-400 va:to-violet-400 va:bg-clip-text va:text-transparent">.</span>
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {faNum(7)} تماس مانده، {faNum(2)} بازدید و یک قرارداد برای امروز.
        </p>
      </div>
      {actions}
    </section>
  );
}

/** Eight-point star lattice, the shape of Persian girih tiles, very faint. */
function TilePattern() {
  return (
    <svg aria-hidden className="absolute inset-0 size-full opacity-[0.13]" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <pattern id="girih" width="56" height="56" patternUnits="userSpaceOnUse">
          <path
            d="M28 4l6 10 11-3-3 11 10 6-10 6 3 11-11-3-6 10-6-10-11 3 3-11-10-6 10-6-3-11 11 3z"
            fill="none"
            stroke="white"
            strokeWidth="1.2"
          />
          <circle cx="28" cy="28" r="5" fill="none" stroke="white" strokeWidth="1" />
        </pattern>
      </defs>
      <rect width="100%" height="100%" fill="url(#girih)" />
    </svg>
  );
}

/* ───────────────────────── KPIs ───────────────────────── */

const KPI_ICON: Record<KpiKey, React.ComponentType<{ className?: string }>> = {
  listings: Building2, leads: Users, hot: Flame, calls: PhoneCall, deals: Handshake, conversion: Target,
};
const KPI_TINT: Record<KpiKey, string> = {
  listings: "text-chart-1 bg-chart-1/12",
  leads: "text-chart-2 bg-chart-2/12",
  hot: "text-chart-3 bg-chart-3/12",
  calls: "text-chart-4 bg-chart-4/12",
  deals: "text-chart-5 bg-chart-5/12",
  conversion: "text-primary bg-primary/12",
};

function Sparkline({ seed, id }: { seed: number; id: string }) {
  const data = spark(seed);
  return (
    <ChartContainer config={{ v: { label: "", color: "var(--primary)" } }} className="aspect-auto h-10 w-full">
      <AreaChart data={data} margin={{ top: 4, bottom: 0, left: 0, right: 0 }}>
        <defs>
          <linearGradient id={`sp-${id}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-v)" stopOpacity={0.35} />
            <stop offset="100%" stopColor="var(--color-v)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis dataKey="i" hide reversed />
        <Area type="monotone" dataKey="v" stroke="var(--color-v)" strokeWidth={1.75} fill={`url(#sp-${id})`} isAnimationActive={false} />
      </AreaChart>
    </ChartContainer>
  );
}

function KpiValue({ k }: { k: (typeof kpis)[number] }) {
  return (
    <span className="tabular">
      <CountUp value={k.value} digits={k.unit ? 1 : 0} />
      {k.unit}
      {k.of && <span className="text-base font-medium text-muted-foreground"> از {faNum(k.of)}</span>}
    </span>
  );
}

function Kpis({ variant }: { variant: Variant }) {
  if (variant === "b") {
    return (
      <section className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border bg-border md:grid-cols-3 2xl:grid-cols-6">
        {kpis.map((k) => (
          <div key={k.key} className="flex flex-col gap-1.5 bg-card p-4">
            <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
              <span className="truncate">{k.label}</span>
              <Delta value={k.delta} />
            </div>
            <div className="text-2xl font-bold tracking-tight">
              <KpiValue k={k} />
            </div>
            <div className="truncate text-[11px] text-muted-foreground">{k.hint}</div>
          </div>
        ))}
      </section>
    );
  }

  return (
    <section className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-3 2xl:grid-cols-6">
      {kpis.map((k, i) => {
        const Icon = KPI_ICON[k.key];
        return (
          <Reveal key={k.key} delay={i * 0.05}>
          <Tilt
            className={cn(
              "relative flex h-full flex-col gap-3 overflow-hidden rounded-2xl border bg-card p-4",
              "va:shadow-sm va:dark:bg-linear-to-b va:dark:from-white/[0.04] va:dark:to-transparent va:dark:shadow-none",
              "vc:rounded-[1.75rem] vc:border-transparent vc:p-5 vc:shadow-[0_1px_2px_rgb(60_40_10/0.05),0_12px_32px_-18px_rgb(60_40_10/0.25)] vc:dark:border-border",
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <div
                className={cn(
                  "grid size-10 shrink-0 place-items-center rounded-xl",
                  KPI_TINT[k.key],
                  "va:ring-1 va:ring-inset va:ring-current/20 va:shadow-[0_0_20px_-6px_currentColor]",
                  "vc:size-11 vc:rounded-2xl",
                )}
              >
                <Icon className="size-5" />
              </div>
              <Delta value={k.delta} />
            </div>
            <div>
              <div className="text-[13px] text-muted-foreground">{k.label}</div>
              <div className="mt-1 text-[26px] leading-none font-black tracking-tight vc:text-3xl">
                <KpiValue k={k} />
              </div>
            </div>
            {variant === "a" ? (
              <div className="-mx-1 -mb-1">
                <Sparkline seed={k.seed} id={k.key} />
              </div>
            ) : (
              <div className="text-xs text-muted-foreground">{k.hint}</div>
            )}
          </Tilt>
          </Reveal>
        );
      })}
    </section>
  );
}

/* ───────────────────────── charts ───────────────────────── */

const trendConfig = {
  listings: { label: "آگهی تازه", color: "var(--chart-1)" },
  leads: { label: "لید", color: "var(--chart-2)" },
} satisfies ChartConfig;

function TrendPanel() {
  const totals = trend.reduce((a, d) => ({ l: a.l + d.listings, d: a.d + d.deals }), { l: 0, d: 0 });
  return (
    <Panel
      title="روند آگهی و لید"
      hint={`${faNum(totals.l)} آگهی و ${faNum(totals.d)} قرارداد در ۳۰ روز گذشته`}
      action={
        <Tabs defaultValue="30">
          <TabsList className="h-8">
            <TabsTrigger value="7" className="px-2.5 text-xs">۷ روز</TabsTrigger>
            <TabsTrigger value="30" className="px-2.5 text-xs">۳۰ روز</TabsTrigger>
            <TabsTrigger value="90" className="px-2.5 text-xs">۹۰ روز</TabsTrigger>
          </TabsList>
        </Tabs>
      }
    >
      <div className="mb-3 flex flex-wrap gap-4 text-xs text-muted-foreground">
        {Object.entries(trendConfig).map(([k, c]) => (
          <span key={k} className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm" style={{ background: c.color }} />
            {c.label}
          </span>
        ))}
      </div>
      <ChartContainer config={trendConfig} className="aspect-auto h-[250px] w-full">
        <AreaChart data={trend} margin={{ top: 4, left: 4, right: 4, bottom: 0 }}>
          <defs>
            {Object.keys(trendConfig).map((k) => (
              <linearGradient key={k} id={`tr-${k}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={`var(--color-${k})`} stopOpacity={0.3} />
                <stop offset="100%" stopColor={`var(--color-${k})`} stopOpacity={0.02} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid vertical={false} strokeDasharray="3 3" />
          <XAxis dataKey="day" reversed tickLine={false} axisLine={false} tickMargin={8} minTickGap={28} />
          <YAxis orientation="right" tickLine={false} axisLine={false} width={36} tickFormatter={(v: number) => faNum(v)} />
          <ChartTooltip cursor={false} content={<ChartTooltipContent indicator="dot" />} />
          <Area type="monotone" dataKey="listings" stroke="var(--color-listings)" strokeWidth={2} fill="url(#tr-listings)" />
          <Area type="monotone" dataKey="leads" stroke="var(--color-leads)" strokeWidth={2} fill="url(#tr-leads)" />
        </AreaChart>
      </ChartContainer>
    </Panel>
  );
}

function FunnelPanel() {
  const top = funnel[0].value;
  return (
    <Panel title="قیف فروش" hint="از لید تا قرارداد، ۳۰ روز" action={<MoreLink>گزارش</MoreLink>}>
      <ol className="flex flex-col gap-3.5">
        {funnel.map((f, i) => {
          const pct = (f.value / top) * 100;
          const step = i === 0 ? null : (f.value / funnel[i - 1].value) * 100;
          return (
            <li key={f.stage} className="flex flex-col gap-1.5">
              <div className="flex items-baseline justify-between gap-2 text-sm">
                <span className="font-medium">{f.stage}</span>
                <span className="flex items-baseline gap-2 tabular">
                  {step !== null && <span className="text-[11px] text-muted-foreground">{faPercent(step, 0)} از قبلی</span>}
                  <span className="font-bold">{faNum(f.value)}</span>
                </span>
              </div>
              <div className="h-2.5 overflow-hidden rounded-full bg-muted vb:h-2 vb:rounded-sm">
                <div
                  className="h-full rounded-full bg-primary vb:rounded-sm va:bg-linear-to-l va:from-indigo-500 va:to-violet-500 vc:bg-linear-to-l vc:from-teal-500 vc:to-amber-400"
                  style={{ width: `${Math.max(pct, 3)}%`, opacity: 1 - i * 0.12 }}
                />
              </div>
            </li>
          );
        })}
      </ol>
      <div className="mt-5 grid grid-cols-2 gap-3 border-t pt-4 text-center">
        <div>
          <div className="text-lg font-black tabular">{faPercent(6.8)}</div>
          <div className="text-[11px] text-muted-foreground">تبدیل کل</div>
        </div>
        <div>
          <div className="text-lg font-black tabular">{toman(480_000_000, false)}</div>
          <div className="text-[11px] text-muted-foreground">کمیسیون ماه (تومان)</div>
        </div>
      </div>
    </Panel>
  );
}

/* ───────────────────────── today ───────────────────────── */

const CALL_STATE: Record<CallItem["status"], { label: string; cls: string; Icon: React.ComponentType<{ className?: string }> }> = {
  done: { label: "انجام شد", cls: "text-success bg-success/12", Icon: Check },
  missed: { label: "جواب نداد", cls: "text-destructive bg-destructive/12", Icon: PhoneMissed },
  due: { label: "نوبت تماس", cls: "text-warning bg-warning/15", Icon: Clock },
  later: { label: "بعداً", cls: "text-muted-foreground bg-muted", Icon: CircleDashed },
};
const TEMP: Record<CallItem["temp"], string> = { hot: "bg-destructive", warm: "bg-warning", cold: "bg-info" };

function CallsPanel() {
  return (
    <Panel title="تماس‌های امروز" hint={`${faNum(24)} از ${faNum(31)} انجام شده`} action={<MoreLink />}>
      <Progress value={(24 / 31) * 100} className="mb-4 h-1.5" />
      <ul className="-mx-2 flex flex-col">
        {calls.map((c) => {
          const s = CALL_STATE[c.status];
          return (
            <li key={c.name} className="flex items-center gap-3 rounded-xl px-2 py-2.5 hover:bg-accent/60 vb:rounded-md vb:py-2">
              <div className="relative">
                <Avatar className="size-9 vb:size-8">
                  <AvatarFallback className="text-sm font-semibold">{initials(c.name)}</AvatarFallback>
                </Avatar>
                <span className={cn("absolute -bottom-0.5 -end-0.5 size-3 rounded-full ring-2 ring-card", TEMP[c.temp])} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-semibold">{c.name}</span>
                  <span className="text-[11px] text-muted-foreground tabular">{c.time}</span>
                </div>
                <div className="truncate text-xs text-muted-foreground">{c.topic}</div>
              </div>
              <span className={cn("hidden items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium sm:inline-flex", s.cls)}>
                <s.Icon className="size-3" />
                {s.label}
              </span>
              <Button size="icon" variant="ghost" className="size-8 shrink-0 rounded-full" aria-label={`تماس با ${c.name}`}>
                <Phone className="size-4" />
              </Button>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

function ScoreRing({ score }: { score: number }) {
  return (
    <div
      className="grid size-12 shrink-0 place-items-center rounded-full"
      style={{ background: `conic-gradient(var(--primary) ${score * 3.6}deg, var(--muted) 0)` }}
    >
      <div className="grid size-[38px] place-items-center rounded-full bg-card text-xs font-black tabular">
        {faNum(score)}٪
      </div>
    </div>
  );
}

function MatchesPanel() {
  return (
    <Panel title="تطبیق‌های تازه" hint="ملک مناسب برای مشتری‌های شما" action={<MoreLink />}>
      <ul className="flex flex-col gap-3">
        {matches.map((m) => (
          <li key={m.customer} className="flex gap-3 rounded-xl border p-3 vb:rounded-md vc:rounded-2xl vc:border-transparent vc:bg-muted/60">
            <ScoreRing score={m.score} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-sm font-semibold">{m.customer}</span>
                <span className="text-xs text-muted-foreground">{toman(m.price)}</span>
              </div>
              <div className="mt-0.5 truncate text-xs text-muted-foreground">{m.property}</div>
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                {m.reasons.map((r) => (
                  <Badge key={r} variant="secondary" className="h-5 rounded-full px-2 text-[10px] font-medium">
                    {r}
                  </Badge>
                ))}
                <div className="ms-auto flex gap-1">
                  <Button size="icon" variant="ghost" className="size-7" aria-label="پیامک به مشتری">
                    <MessageCircle className="size-3.5" />
                  </Button>
                  <Button size="icon" variant="ghost" className="size-7" aria-label="تماس با مشتری">
                    <Phone className="size-3.5" />
                  </Button>
                </div>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

const AGENDA_ICON = { visit: Building2, contract: PenLine, task: Camera, meeting: UsersRound } as const;

function AgendaPanel() {
  return (
    <Panel title="برنامهٔ امروز" hint="قرارها و کارهای زمان‌دار" action={<MoreLink>تقویم</MoreLink>}>
      <ol className="relative flex flex-col gap-4 before:absolute before:inset-y-2 before:start-[15px] before:w-px before:bg-border">
        {agenda.map((a) => {
          const Icon = AGENDA_ICON[a.kind];
          return (
            <li key={a.title} className="relative flex gap-3">
              <div className="z-10 grid size-8 shrink-0 place-items-center rounded-full border bg-card text-primary">
                <Icon className="size-4" />
              </div>
              <div className="min-w-0 pt-0.5">
                <div className="text-[11px] font-semibold text-primary tabular">{a.time}</div>
                <div className="text-sm font-semibold">{a.title}</div>
                <div className="truncate text-xs text-muted-foreground">{a.who}</div>
              </div>
            </li>
          );
        })}
      </ol>
    </Panel>
  );
}

/* ───────────────────────── market & team ───────────────────────── */

function SkylinePanel() {
  return (
    <Panel title="بازار محله‌ها" hint="ارتفاع ساختمان: آگهی فعال · رنگ: قیمت هر متر" action={<MoreLink>بینش بازار</MoreLink>}>
      <Skyline data={districts} />
    </Panel>
  );
}

function LeadSourcesPanel() {
  return (
    <Panel title="منبع لیدها" hint="۳۰ روز گذشته" action={<MoreLink>گزارش</MoreLink>}>
      <Donut3D data={leadSources} centerLabel="لید در ۳۰ روز" />
    </Panel>
  );
}

function TargetPanel() {
  const month = faDate(new Date(), { month: "long", year: "numeric" });
  return (
    <Panel title="هدف ماه" hint={month}>
      <TargetGauge items={targets} />
    </Panel>
  );
}

function HeatmapPanel() {
  return (
    <Panel title="ساعت‌های طلایی تماس" hint="تماس‌های جواب‌گرفته در ۴ هفتهٔ گذشته، به تفکیک روز و ساعت">
      <CallHeatmap grid={callGrid} />
      <p className="mt-2 text-xs leading-6 text-muted-foreground">
        بیشترین جواب: <span className="font-semibold text-foreground">شنبه تا دوشنبه، ساعت ۱۰ تا ۱۲</span>. عصرها بین ۱۷ تا ۱۹ هم خوب جواب می‌دهد.
      </p>
    </Panel>
  );
}

function DealsPanel() {
  return (
    <Panel title="قراردادها به تفکیک نوع" hint="۶ ماه گذشته" action={<MoreLink>گزارش</MoreLink>}>
      <DealsByMonth data={dealsByMonth} />
    </Panel>
  );
}

function RadarPanel() {
  return (
    <Panel title="مقایسهٔ عملکرد" hint="بهترین مشاور در برابر میانگین تیم، از ۱۰۰">
      <TeamRadar data={teamRadar} />
    </Panel>
  );
}

const PRESENCE = { available: "bg-success", busy: "bg-warning", away: "bg-muted-foreground" } as const;

function TeamPanel() {
  return (
    <Panel title="عملکرد تیم" hint="این هفته" action={<MoreLink>گزارش کامل</MoreLink>} bodyClassName="px-0 vb:px-0 vc:px-0 pb-2">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="ps-5 vc:ps-6">مشاور</TableHead>
            <TableHead className="text-center">تماس</TableHead>
            <TableHead className="text-center">بازدید</TableHead>
            <TableHead className="text-center">قرارداد</TableHead>
            <TableHead className="pe-5 text-center vc:pe-6">پاسخ‌گویی</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {team.map((t) => (
            <TableRow key={t.name}>
              <TableCell className="ps-5 vc:ps-6">
                <div className="flex items-center gap-2.5">
                  <div className="relative">
                    <Avatar className="size-8">
                      <AvatarFallback className="text-xs font-semibold">{initials(t.name)}</AvatarFallback>
                    </Avatar>
                    <span className={cn("absolute -bottom-0.5 -end-0.5 size-2.5 rounded-full ring-2 ring-card", PRESENCE[t.status])} />
                  </div>
                  <div className="leading-tight">
                    <div className="text-sm font-semibold">{t.name}</div>
                    <div className="text-[11px] text-muted-foreground">{t.role}</div>
                  </div>
                </div>
              </TableCell>
              <TableCell className="text-center tabular">{faNum(t.calls)}</TableCell>
              <TableCell className="text-center tabular">{faNum(t.visits)}</TableCell>
              <TableCell className="text-center font-bold tabular">{faNum(t.deals)}</TableCell>
              <TableCell className="pe-5 text-center tabular vc:pe-6">
                <span className={cn(t.response > 15 && "text-warning")}>{faNum(t.response)} دقیقه</span>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Panel>
  );
}

/* ───────────────────────── system & activity ───────────────────────── */

function SystemPanel() {
  const tiles = [
    {
      Icon: Radar, title: "اسکرپر",
      value: `${faNum(system.scraper.running)} در حال اجرا`,
      sub: `${faNum(system.scraper.queued)} در صف، آخرین: ${system.scraper.lastRun}`,
      progress: system.scraper.progress, tone: "text-chart-1 bg-chart-1/12",
    },
    {
      Icon: KeyRound, title: "شماره‌های دیوار",
      value: `${faNum(system.divar.healthy)} از ${faNum(system.divar.total)} سالم`,
      sub: "یک شماره کد تأیید می‌خواهد", warn: true, tone: "text-warning bg-warning/15",
    },
    {
      Icon: DatabaseBackup, title: "بکاپ کامل",
      value: "موفق", sub: `آخرین: ${system.backup.last}`, tone: "text-success bg-success/12",
    },
    {
      Icon: Sparkles, title: "هوش مصنوعی",
      value: `${faPercent(system.ai.used, 0)} بودجهٔ امروز`, sub: "همهٔ ایجنت‌ها روشن",
      progress: system.ai.used, tone: "text-chart-2 bg-chart-2/12",
    },
  ];
  return (
    <Panel title="وضعیت سامانه" hint="به‌روز شده ۱ دقیقه پیش" action={<MoreLink>پایش کامل</MoreLink>}>
      <div className="grid gap-3 sm:grid-cols-2">
        {tiles.map((t) => (
          <div key={t.title} className="flex gap-3 rounded-xl border p-3 vb:rounded-md vc:rounded-2xl vc:border-transparent vc:bg-muted/60">
            <div className={cn("grid size-9 shrink-0 place-items-center rounded-lg", t.tone)}>
              <t.Icon className="size-[18px]" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-xs text-muted-foreground">{t.title}</div>
              <div className={cn("text-sm font-bold", t.warn && "text-warning")}>{t.value}</div>
              <div className="truncate text-[11px] text-muted-foreground">{t.sub}</div>
              {t.progress !== undefined && <Progress value={t.progress} className="mt-2 h-1" />}
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

const ACT_ICON = { deal: Handshake, scrape: Bot, call: PhoneCall, match: Sparkles, lead: Users } as const;

function ActivityPanel() {
  return (
    <Panel title="آخرین رویدادها" action={<MoreLink />}>
      <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {activity.map((a) => {
          const Icon = ACT_ICON[a.kind];
          return (
            <li key={a.what + a.when} className="flex gap-3">
              <div className="grid size-8 shrink-0 place-items-center rounded-full bg-muted text-muted-foreground">
                <Icon className="size-4" />
              </div>
              <div className="min-w-0 text-sm leading-6">
                <span className="font-semibold">{a.who}</span> {a.what}
                <span className="text-muted-foreground"> — {a.target}</span>
                <div className="text-[11px] text-muted-foreground">{a.when}</div>
              </div>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

/* ───────────────────────── page ───────────────────────── */

function Row({ className, children }: { className?: string; children: React.ReactNode }) {
  // grid-cols-1 is minmax(0,1fr): a wide child (the heatmap) scrolls inside
  // its panel instead of widening the whole page on a phone.
  return <div className={cn("grid grid-cols-1 gap-5 vb:gap-4 vc:gap-6", className)}>{children}</div>;
}

export function Dashboard({ variant }: { variant: Variant }) {
  return (
    <div className="mx-auto flex max-w-[1480px] flex-col gap-5 vb:gap-4 vc:gap-6">
      <Reveal>
        <Greeting variant={variant} />
      </Reveal>
      <Kpis variant={variant} />
      <Row className="xl:grid-cols-12">
        <Reveal className="xl:col-span-8"><TrendPanel /></Reveal>
        <Reveal className="xl:col-span-4" delay={0.08}><LeadSourcesPanel /></Reveal>
      </Row>
      <Row className="xl:grid-cols-12">
        <Reveal className="xl:col-span-8"><SkylinePanel /></Reveal>
        <Reveal className="xl:col-span-4" delay={0.08}><TargetPanel /></Reveal>
      </Row>
      <Row className="lg:grid-cols-2 xl:grid-cols-12">
        <Reveal className="xl:col-span-5"><CallsPanel /></Reveal>
        <Reveal className="xl:col-span-4" delay={0.06}><MatchesPanel /></Reveal>
        <Reveal className="lg:col-span-2 xl:col-span-3" delay={0.12}><AgendaPanel /></Reveal>
      </Row>
      <Row className="xl:grid-cols-12">
        <Reveal className="xl:col-span-7"><HeatmapPanel /></Reveal>
        <Reveal className="xl:col-span-5" delay={0.08}><FunnelPanel /></Reveal>
      </Row>
      <Row className="xl:grid-cols-12">
        <Reveal className="xl:col-span-7"><TeamPanel /></Reveal>
        <Reveal className="xl:col-span-5" delay={0.08}><RadarPanel /></Reveal>
      </Row>
      <Row className="xl:grid-cols-12">
        <Reveal className="xl:col-span-6"><DealsPanel /></Reveal>
        <Reveal className="xl:col-span-6" delay={0.08}><SystemPanel /></Reveal>
      </Row>
      <Reveal><ActivityPanel /></Reveal>
      <p className="flex items-center justify-center gap-1.5 pb-2 text-center text-xs text-muted-foreground">
        <CalendarDays className="size-3.5" />
        دادهٔ این صفحه نمونه و ساختگی است، فقط برای انتخاب ظاهر.
      </p>
    </div>
  );
}
