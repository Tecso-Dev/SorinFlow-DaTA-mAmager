"use client";

import {
  ArrowUpLeft, Bot, Building2, CalendarDays, Check, CircleDashed, Clock, Database, Flame, Handshake,
  KeyRound, MessageCircle, PenLine, Phone, PhoneCall, PhoneMissed, Plus, Radar, RefreshCw, Server, Target,
  TrendingDown, TrendingUp, Users, UsersRound,
} from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts";
import { cn } from "cn";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import {
  change, useOverview, type CallsToday, type Health, type JobsSummary, type LeadPage,
  type Matches, type Overview, type PropertyPage, type Upcoming,
} from "@/lib/dashboard";
import { faDate, faNum, faPercent, parseDigits, toman } from "@/lib/format";
import { can, displayName, useSession, type User } from "@/lib/session";
import {
  CallHeatmap, CountUp, DealsByMonth, Donut3D, Reveal, Skyline, TargetGauge, TeamRadar, Tilt, type DealMonth,
} from "@/components/viz";

/* ───────────────────────── building blocks ───────────────────────── */

function Panel({
  title, hint, action, className, bodyClassName, children,
}: {
  title?: string; hint?: React.ReactNode; action?: React.ReactNode; className?: string; bodyClassName?: string;
  children: React.ReactNode;
}) {
  return (
    <section
      className={cn(
        "flex h-full min-w-0 flex-col rounded-2xl border bg-card text-card-foreground",
        "shadow-sm dark:bg-linear-to-b dark:from-white/[0.035] dark:to-white/[0.008] dark:shadow-none",
        className,
      )}
    >
      {title && (
        <header className="flex items-start justify-between gap-3 px-5 pt-4">
          <div className="min-w-0">
            <h2 className="text-[15px] font-bold">{title}</h2>
            {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
          </div>
          {action}
        </header>
      )}
      <div className={cn("flex-1 p-5 pt-3", bodyClassName)}>{children}</div>
    </section>
  );
}

/** Sections not rebuilt yet open in the current panel. */
const legacy = (section: string) => `/dashboard/#/${section}`;

function MoreLink({ href, children = "همه" }: { href: string; children?: React.ReactNode }) {
  return (
    <Button asChild variant="ghost" size="sm" className="h-7 gap-1 px-2 text-xs text-muted-foreground hover:text-foreground">
      <a href={href}>
        {children}
        <ArrowUpLeft className="size-3.5" />
      </a>
    </Button>
  );
}

function Delta({ value, unit = "٪" }: { value: number | null; unit?: string }) {
  if (value === null || !Number.isFinite(value)) return null;
  const up = value >= 0;
  const Icon = up ? TrendingUp : TrendingDown;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[11px] font-semibold tabular",
        up ? "bg-success/12 text-success" : "bg-destructive/12 text-destructive",
      )}
    >
      {/* charts run right-to-left here, so a rising arrow points up-left */}
      <Icon className="size-3 -scale-x-100" />
      {faNum(Math.abs(value), { maximumFractionDigits: 1 })}
      {unit}
    </span>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-28 place-items-center rounded-xl border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">
      {children}
    </div>
  );
}

function PanelError({ error }: { error: unknown }) {
  const msg = error instanceof ApiError ? error.message : "بارگیری ناموفق بود";
  return <Empty>{msg}</Empty>;
}

const initials = (name: string) => name.replace(/^(خانوادهٔ|آقای|خانم)\s+/, "").trim().slice(0, 1) || "؟";
const day = (iso: string) => new Date(`${iso}T12:00:00`);
const time = (iso: string) => faDate(new Date(iso), { hour: "2-digit", minute: "2-digit" });

/** «۵ دقیقه پیش» — how long ago, in words. */
function ago(iso: string | null | undefined): string {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "همین حالا";
  if (s < 3600) return `${faNum(Math.floor(s / 60))} دقیقه پیش`;
  if (s < 86400) return `${faNum(Math.floor(s / 3600))} ساعت پیش`;
  return `${faNum(Math.floor(s / 86400))} روز پیش`;
}

function isToday(iso: string) {
  const d = new Date(iso);
  const n = new Date();
  return d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate();
}

/* ───────────────────────── data for the side panels ───────────────────────── */

function useSide<T>(key: string, path: string, enabled: boolean) {
  return useQuery({
    queryKey: ["dashboard", key],
    queryFn: () => api<T>(path),
    enabled,
    refetchInterval: 60_000,
    retry: false,
  });
}

/* ───────────────────────── header ───────────────────────── */

function Greeting({ user, ov, upcoming }: { user: User; ov?: Overview; upcoming?: Upcoming }) {
  const date = faDate(new Date(), { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  const first = displayName(user).split(" ")[0];
  const visits = (upcoming?.items ?? []).filter((e) => isToday(e.start_at) && (e.event_type ?? e.kind) === "visit").length;
  const parts: string[] = [];
  if (ov) parts.push(ov.kpis.calls_due ? `${faNum(ov.kpis.calls_due)} تماس در صف` : "صف تماس خالی است");
  if (upcoming) parts.push(visits ? `${faNum(visits)} بازدید امروز` : "بازدیدی برای امروز ثبت نشده");
  return (
    <section className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
      <div>
        <p className="text-sm text-muted-foreground">{date}</p>
        <h1 className="mt-1 text-2xl font-black tracking-tight">
          روز بخیر، {first}
          <span className="bg-linear-to-l from-indigo-400 to-violet-400 bg-clip-text text-transparent">.</span>
        </h1>
        {parts.length > 0 && <p className="mt-1 text-sm text-muted-foreground">{parts.join("، ")}.</p>}
      </div>
      <div className="flex flex-wrap gap-2">
        {can(user, { perm: "crm" }) && (
          <>
            <Button asChild className="gap-1.5 shadow-[0_8px_24px_-8px_rgb(99_102_241/0.8)]">
              <a href={legacy("crm")}><Plus className="size-4" /> لید تازه</a>
            </Button>
            <Button asChild variant="outline" className="gap-1.5">
              <a href={legacy("crm")}><Users className="size-4" /> مشتری تازه</a>
            </Button>
          </>
        )}
        {can(user, { perm: "scraper" }) && (
          <Button asChild variant="outline" className="gap-1.5">
            <a href={legacy("scraper")}><Bot className="size-4" /> اسکرپ تازه</a>
          </Button>
        )}
      </div>
    </section>
  );
}

/* ───────────────────────── KPIs ───────────────────────── */

type Kpi = {
  key: string;
  label: string;
  value: number | null;
  digits?: number;
  unit?: string;
  of?: number;
  delta: number | null;
  deltaUnit?: string;
  hint: string;
  series?: number[];
  Icon: React.ComponentType<{ className?: string }>;
  tint: string;
};

function Sparkline({ values, id }: { values: number[]; id: string }) {
  const data = values.map((v, i) => ({ i, v }));
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

function kpisOf(ov: Overview): Kpi[] {
  const k = ov.kpis;
  const last = (key: "listings" | "leads" | "deals") => ov.trend.slice(-14).map((d) => d[key]);
  const days = faNum(ov.days);
  return [
    {
      key: "listings", label: "آگهی‌های تازهٔ امروز", value: k.listings_today,
      delta: change(k.listings_today, k.listings_yesterday), hint: `دیروز ${faNum(k.listings_yesterday)}`,
      series: last("listings"), Icon: Building2, tint: "text-chart-1 bg-chart-1/12",
    },
    {
      key: "leads", label: "لیدهای باز", value: k.leads_open,
      delta: change(k.leads_new, k.leads_new_before), hint: `${faNum(k.leads_new)} لید تازه در ${days} روز`,
      series: last("leads"), Icon: Users, tint: "text-chart-2 bg-chart-2/12",
    },
    {
      key: "hot", label: "مشتریان داغ", value: k.hot_customers, delta: null,
      hint: ov.scope === "office" ? "همهٔ مشتری‌های دفتر" : "مشتری‌های شما و بی‌مشاور", Icon: Flame,
      tint: "text-chart-3 bg-chart-3/12",
    },
    {
      key: "calls", label: "تماس‌های امروز", value: k.calls_today, of: k.calls_today + k.calls_due,
      delta: change(k.calls_today, k.calls_yesterday),
      hint: k.calls_due ? `${faNum(k.calls_due)} تماس در صف` : "صف تماس خالی است", Icon: PhoneCall,
      tint: "text-chart-4 bg-chart-4/12",
    },
    {
      key: "deals", label: `قرارداد ${days} روز`, value: k.deals, delta: change(k.deals, k.deals_before),
      hint: k.commission ? `کمیسیون: ${toman(k.commission)}` : "کمیسیونی ثبت نشده", series: last("deals"),
      Icon: Handshake, tint: "text-chart-5 bg-chart-5/12",
    },
    {
      key: "conversion", label: "نرخ تبدیل لید", value: k.conversion, digits: 1, unit: "٪",
      delta: k.conversion !== null && k.conversion_before !== null
        ? Math.round((k.conversion - k.conversion_before) * 10) / 10 : null,
      deltaUnit: " واحد", hint: `لید به قرارداد، ${days} روز`, Icon: Target, tint: "text-primary bg-primary/12",
    },
  ];
}

function Kpis({ ov }: { ov: Overview }) {
  return (
    <section className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-3 2xl:grid-cols-6">
      {kpisOf(ov).map((k, i) => (
        <Reveal key={k.key} delay={i * 0.05}>
          <Tilt
            className={cn(
              "relative flex h-full flex-col gap-3 overflow-hidden rounded-2xl border bg-card p-4",
              "shadow-sm dark:bg-linear-to-b dark:from-white/[0.04] dark:to-transparent dark:shadow-none",
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <div className={cn("grid size-10 shrink-0 place-items-center rounded-xl ring-1 ring-inset ring-current/20 shadow-[0_0_20px_-6px_currentColor]", k.tint)}>
                <k.Icon className="size-5" />
              </div>
              <Delta value={k.delta} unit={k.deltaUnit} />
            </div>
            <div>
              <div className="text-[13px] text-muted-foreground">{k.label}</div>
              <div className="mt-1 text-[26px] leading-none font-black tracking-tight tabular">
                {k.value === null ? "—" : <CountUp value={k.value} digits={k.digits ?? 0} />}
                {k.value !== null && k.unit}
                {k.of !== undefined && k.of > 0 && (
                  <span className="text-base font-medium text-muted-foreground"> از {faNum(k.of)}</span>
                )}
              </div>
              <div className="mt-1.5 truncate text-[11px] text-muted-foreground">{k.hint}</div>
            </div>
            {k.series && k.series.some(Boolean) && (
              <div className="-mx-1 -mb-1 mt-auto">
                <Sparkline values={k.series} id={k.key} />
              </div>
            )}
          </Tilt>
        </Reveal>
      ))}
    </section>
  );
}

/* ───────────────────────── charts ───────────────────────── */

const trendConfig = {
  listings: { label: "آگهی تازه", color: "var(--chart-1)" },
  leads: { label: "لید", color: "var(--chart-2)" },
} satisfies ChartConfig;

function TrendPanel({ ov, days, onDays }: { ov: Overview; days: number; onDays: (d: number) => void }) {
  const totals = ov.trend.reduce((a, d) => ({ l: a.l + d.listings, d: a.d + d.deals }), { l: 0, d: 0 });
  const data = ov.trend.map((d) => ({ ...d, day: faDate(day(d.date), { day: "numeric", month: "short" }) }));
  return (
    <Panel
      title="روند آگهی و لید"
      hint={`${faNum(totals.l)} آگهی و ${faNum(totals.d)} قرارداد در ${faNum(ov.days)} روز گذشته`}
      action={
        <Tabs value={String(days)} onValueChange={(v) => onDays(Number(v))}>
          <TabsList className="h-8">
            {[7, 30, 90].map((d) => (
              <TabsTrigger key={d} value={String(d)} className="px-2.5 text-xs">{faNum(d)} روز</TabsTrigger>
            ))}
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
        <AreaChart data={data} margin={{ top: 4, left: 4, right: 4, bottom: 0 }}>
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
          <YAxis orientation="right" tickLine={false} axisLine={false} width={36} allowDecimals={false} tickFormatter={(v: number) => faNum(v)} />
          <ChartTooltip cursor={false} content={<ChartTooltipContent indicator="dot" />} />
          <Area type="monotone" dataKey="listings" stroke="var(--color-listings)" strokeWidth={2} fill="url(#tr-listings)" />
          <Area type="monotone" dataKey="leads" stroke="var(--color-leads)" strokeWidth={2} fill="url(#tr-leads)" />
        </AreaChart>
      </ChartContainer>
    </Panel>
  );
}

function FunnelPanel({ ov }: { ov: Overview }) {
  const top = ov.funnel[0]?.count ?? 0;
  return (
    <Panel title="قیف فروش" hint="همهٔ لیدها، از تازه تا قرارداد" action={<MoreLink href={legacy("crm")}>لیدها</MoreLink>}>
      {top === 0 ? (
        <Empty>هنوز لیدی ثبت نشده است.</Empty>
      ) : (
        <ol className="flex flex-col gap-3.5">
          {ov.funnel.map((f, i) => {
            const pct = (f.count / top) * 100;
            const prev = ov.funnel[i - 1]?.count;
            const step = i === 0 || !prev ? null : (f.count / prev) * 100;
            return (
              <li key={f.key} className="flex flex-col gap-1.5">
                <div className="flex items-baseline justify-between gap-2 text-sm">
                  <span className="font-medium">{f.label}</span>
                  <span className="flex items-baseline gap-2 tabular">
                    {step !== null && <span className="text-[11px] text-muted-foreground">{faPercent(step, 0)} از قبلی</span>}
                    <span className="font-bold">{faNum(f.count)}</span>
                  </span>
                </div>
                <div className="h-2.5 overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-linear-to-l from-indigo-500 to-violet-500"
                    style={{ width: `${Math.max(pct, f.count ? 3 : 0)}%`, opacity: 1 - i * 0.12 }}
                  />
                </div>
              </li>
            );
          })}
        </ol>
      )}
      <div className="mt-5 grid grid-cols-2 gap-3 border-t pt-4 text-center">
        <div>
          <div className="text-lg font-black tabular">{ov.kpis.conversion === null ? "—" : faPercent(ov.kpis.conversion)}</div>
          <div className="text-[11px] text-muted-foreground">تبدیل لیدهای {faNum(ov.days)} روز</div>
        </div>
        <div>
          <div className="text-lg font-black tabular">{ov.kpis.commission ? toman(ov.kpis.commission, false) : "—"}</div>
          <div className="text-[11px] text-muted-foreground">کمیسیون {faNum(ov.days)} روز (تومان)</div>
        </div>
      </div>
    </Panel>
  );
}

/* ───────────────────────── today ───────────────────────── */

const OUTCOME: Record<string, { label: string; cls: string; Icon: React.ComponentType<{ className?: string }> }> = {
  answered: { label: "جواب داد", cls: "text-success bg-success/12", Icon: Check },
  no_answer: { label: "جواب نداد", cls: "text-destructive bg-destructive/12", Icon: PhoneMissed },
  busy: { label: "مشغول بود", cls: "text-destructive bg-destructive/12", Icon: PhoneMissed },
  callback: { label: "تماس دوباره", cls: "text-warning bg-warning/15", Icon: Clock },
};
const DUE = { label: "نوبت تماس", cls: "text-warning bg-warning/15", Icon: Clock };
const FRESH = { label: "تماس اول", cls: "text-muted-foreground bg-muted", Icon: CircleDashed };

function CallsPanel({ q }: { q: ReturnType<typeof useSide<CallsToday>> }) {
  const d = q.data;
  const all = d ? d.done_today + d.total : 0;
  return (
    <Panel
      title="صف تماس امروز"
      hint={d ? `${faNum(d.done_today)} تماس گرفته‌اید، ${faNum(d.total)} در صف` : undefined}
      action={<MoreLink href={legacy("crm")} />}
    >
      {q.isPending ? (
        <ListSkeleton />
      ) : q.isError ? (
        <PanelError error={q.error} />
      ) : !d || d.items.length === 0 ? (
        <Empty>صف تماس شما خالی است.</Empty>
      ) : (
        <>
          <Progress value={all ? (d.done_today / all) * 100 : 0} className="mb-4 h-1.5" />
          <ul className="-mx-2 flex flex-col">
            {d.items.slice(0, 6).map((c) => {
              const s = c.last_call_outcome ? OUTCOME[c.last_call_outcome] ?? DUE : c.next_call_at ? DUE : FRESH;
              const name = c.seller_name?.trim() || "بدون نام";
              return (
                <li key={c.id} className="flex items-center gap-3 rounded-xl px-2 py-2.5 hover:bg-accent/60">
                  <Avatar className="size-9">
                    <AvatarFallback className="text-sm font-semibold">{initials(name)}</AvatarFallback>
                  </Avatar>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-semibold">{name}</span>
                      {c.next_call_at && <span className="text-[11px] text-muted-foreground tabular">{time(c.next_call_at)}</span>}
                    </div>
                    <div className="truncate text-xs text-muted-foreground">
                      {[c.property_title, c.city_name].filter(Boolean).join(" — ")}
                    </div>
                  </div>
                  <span className={cn("hidden items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium sm:inline-flex", s.cls)}>
                    <s.Icon className="size-3" />
                    {s.label}
                  </span>
                  <Button asChild size="icon" variant="ghost" className="size-8 shrink-0 rounded-full">
                    <a href={legacy("crm")} aria-label={`تماس با ${name}`}>
                      <Phone className="size-4" />
                    </a>
                  </Button>
                </li>
              );
            })}
          </ul>
        </>
      )}
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
        {faNum(Math.round(score))}٪
      </div>
    </div>
  );
}

function MatchesPanel({ q }: { q: ReturnType<typeof useSide<Matches>> }) {
  return (
    <Panel
      title="تطبیق‌های تازه"
      hint={q.data ? `${faNum(q.data.total)} ملک مناسب منتظر تصمیم` : "ملک مناسب برای مشتری‌ها"}
      action={<MoreLink href={legacy("crm")} />}
    >
      {q.isPending ? (
        <ListSkeleton />
      ) : q.isError ? (
        <PanelError error={q.error} />
      ) : !q.data?.items.length ? (
        <Empty>تطبیق تازه‌ای نیست.</Empty>
      ) : (
        <ul className="flex flex-col gap-3">
          {q.data.items.slice(0, 3).map((m) => (
            <li key={m.id} className="flex gap-3 rounded-xl border p-3">
              <ScoreRing score={m.score} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="text-sm font-semibold">{m.customer.full_name || "مشتری"}</span>
                  {m.property.price ? <span className="text-xs text-muted-foreground">{toman(m.property.price)}</span> : null}
                </div>
                <div className="mt-0.5 truncate text-xs text-muted-foreground">
                  {[m.property.title, m.property.district].filter(Boolean).join(" — ")}
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  {m.reasons.slice(0, 3).map((r) => (
                    <Badge key={r} variant="secondary" className="h-5 rounded-full px-2 text-[10px] font-medium">{r}</Badge>
                  ))}
                  <div className="ms-auto flex gap-1">
                    <Button asChild size="icon" variant="ghost" className="size-7">
                      <a href={legacy("crm")} aria-label="پیامک به مشتری"><MessageCircle className="size-3.5" /></a>
                    </Button>
                    <Button asChild size="icon" variant="ghost" className="size-7">
                      <a href={legacy("crm")} aria-label="تماس با مشتری"><Phone className="size-3.5" /></a>
                    </Button>
                  </div>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

const AGENDA_ICON: Record<string, React.ComponentType<{ className?: string }>> = {
  visit: Building2, showing: Building2, meeting: PenLine, call: Phone, task: Check, reminder: Clock,
};

function AgendaPanel({ q }: { q: ReturnType<typeof useSide<Upcoming>> }) {
  return (
    <Panel title="قرارهای پیشِ رو" hint="۷ روز آینده" action={<MoreLink href={legacy("crm")}>تقویم</MoreLink>}>
      {q.isPending ? (
        <ListSkeleton />
      ) : q.isError ? (
        <PanelError error={q.error} />
      ) : !q.data?.items.length ? (
        <Empty>قراری در ۷ روز آینده نیست.</Empty>
      ) : (
        <ol className="relative flex flex-col gap-4 before:absolute before:inset-y-2 before:start-[15px] before:w-px before:bg-border">
          {q.data.items.slice(0, 6).map((a) => {
            const Icon = AGENDA_ICON[a.event_type ?? a.kind ?? ""] ?? UsersRound;
            const when = isToday(a.start_at)
              ? time(a.start_at)
              : faDate(new Date(a.start_at), { weekday: "short", hour: "2-digit", minute: "2-digit" });
            return (
              <li key={`${a.kind ?? "e"}-${a.id}`} className="relative flex gap-3">
                <div className="z-10 grid size-8 shrink-0 place-items-center rounded-full border bg-card text-primary">
                  <Icon className="size-4" />
                </div>
                <div className="min-w-0 pt-0.5">
                  <div className="text-[11px] font-semibold text-primary tabular">{when}</div>
                  <div className="text-sm font-semibold">{a.title}</div>
                  <div className="truncate text-xs text-muted-foreground">
                    {[a.customer_name || a.owner_name, a.assigned_to].filter(Boolean).join(" · ") || a.type_label}
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </Panel>
  );
}

/* ───────────────────────── market & team ───────────────────────── */

function SkylinePanel({ ov }: { ov: Overview }) {
  const data = ov.districts.items.map((d) => ({
    name: d.name, count: d.count, delta: d.delta,
    ppm: d.ppm === null ? null : Math.round(d.ppm / 1e6),
  }));
  return (
    <Panel
      title="بازار محله‌ها"
      hint={ov.districts.city ? `${ov.districts.city}، ${faNum(ov.days)} روز · ارتفاع: آگهی تازه · رنگ: قیمت هر متر` : undefined}
      action={<MoreLink href={legacy("properties")}>املاک</MoreLink>}
    >
      {data.length ? <Skyline data={data} /> : <Empty>در این بازه آگهی با محله ثبت نشده است.</Empty>}
    </Panel>
  );
}

const SOURCE: Record<string, { label: string; color: string }> = {
  divar: { label: "دیوار", color: "var(--chart-1)" },
  portal: { label: "پرتال", color: "var(--chart-2)" },
  referral: { label: "معرف", color: "var(--chart-3)" },
  in_person: { label: "حضوری", color: "var(--chart-5)" },
  unknown: { label: "نامشخص", color: "var(--chart-4)" },
};

function SourcesPanel({ ov }: { ov: Overview }) {
  const data = ov.sources.map((s) => ({
    label: SOURCE[s.key]?.label ?? s.key, value: s.count, color: SOURCE[s.key]?.color ?? "var(--chart-4)",
  }));
  return (
    <Panel title="منبع مشتری‌ها" hint="مشتری‌ها از کجا آمده‌اند" action={<MoreLink href={legacy("crm")}>مشتری‌ها</MoreLink>}>
      {data.length ? <Donut3D data={data} centerLabel="مشتری" /> : <Empty>هنوز مشتری‌ای ثبت نشده است.</Empty>}
    </Panel>
  );
}

function TargetDialog({ ov, open, onOpenChange }: { ov: Overview; open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [deals, setDeals] = useState(ov.target.deals_target ? String(ov.target.deals_target) : "");
  const [fee, setFee] = useState(ov.target.commission_target ? String(ov.target.commission_target / 1e6) : "");
  const [busy, setBusy] = useState(false);
  const num = (s: string) => {
    const n = Number(parseDigits(s).replace(/[,٬\s]/g, ""));
    return Number.isFinite(n) && n > 0 ? n : null;
  };
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const f = num(fee);
      await api("/stats/target", { method: "PUT", json: { deals: num(deals), commission: f ? Math.round(f * 1e6) : null } });
      await qc.invalidateQueries({ queryKey: ["stats", "overview"] });
      toast.success("هدف ماه ذخیره شد");
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "ذخیره نشد");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <form onSubmit={save} className="flex flex-col items-center gap-4 text-center">
          <div className="relative grid size-[62px] place-items-center rounded-full bg-linear-to-br from-indigo-500 to-violet-600 shadow-[0_0_40px_-6px_rgb(99_102_241/0.8)]">
            <div className="absolute inset-[3px] rounded-full bg-card" />
            <Target className="relative size-6 text-primary" />
          </div>
          <div>
            <DialogTitle className="text-lg font-black">هدف این ماه</DialogTitle>
            <DialogDescription className="mt-1 text-sm">داشبورد همه، پیشرفت ماه را با این عددها می‌سنجد.</DialogDescription>
          </div>
          <div className="grid w-full gap-3 text-start">
            <div className="grid gap-1.5">
              <Label htmlFor="t-deals">تعداد قرارداد</Label>
              <Input id="t-deals" dir="ltr" inputMode="numeric" value={deals} onChange={(e) => setDeals(e.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="t-fee">کمیسیون (میلیون تومان)</Label>
              <Input id="t-fee" dir="ltr" inputMode="decimal" value={fee} onChange={(e) => setFee(e.target.value)} />
            </div>
          </div>
          <div className="grid w-full gap-2">
            <Button type="submit" disabled={busy} className="w-full">ذخیره</Button>
            <Button type="button" variant="ghost" className="w-full" onClick={() => onOpenChange(false)}>انصراف</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function TargetPanel({ ov }: { ov: Overview }) {
  const [open, setOpen] = useState(false);
  const t = ov.target;
  const month = faDate(day(t.month_start), { month: "long", year: "numeric" });
  const items = [
    t.deals_target ? { label: "قرارداد", value: t.deals, target: t.deals_target, unit: "", color: "var(--chart-1)" } : null,
    t.commission_target
      ? { label: "کمیسیون", value: Math.round(t.commission / 1e6), target: Math.round(t.commission_target / 1e6), unit: "میلیون", color: "var(--chart-3)" }
      : null,
  ].filter((x): x is NonNullable<typeof x> => x !== null);
  return (
    <Panel
      title="هدف ماه"
      hint={month}
      action={t.can_edit ? (
        <Button variant="ghost" size="sm" className="h-7 gap-1 px-2 text-xs text-muted-foreground" onClick={() => setOpen(true)}>
          <PenLine className="size-3.5" /> تعیین هدف
        </Button>
      ) : undefined}
    >
      {items.length ? (
        <TargetGauge items={items} />
      ) : (
        <Empty>
          <div>
            <div>هدفی برای این ماه تعیین نشده.</div>
            <div className="mt-1 tabular">
              تا امروز {faNum(t.deals)} قرارداد{t.commission ? ` و ${toman(t.commission)} کمیسیون` : ""}.
            </div>
          </div>
        </Empty>
      )}
      {t.can_edit && open && <TargetDialog ov={ov} open={open} onOpenChange={setOpen} />}
    </Panel>
  );
}

const DAY_NAMES = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"];

function HeatmapPanel({ ov }: { ov: Overview }) {
  const rows = ov.call_grid.rows;
  const total = rows.flat().reduce((a, b) => a + b, 0);
  let best = { d: 0, h: 0, v: 0 };
  rows.forEach((r, d) => r.forEach((v, h) => { if (v > best.v) best = { d, h, v }; }));
  return (
    <Panel
      title="ساعت‌های تماس"
      hint={`${ov.scope === "office" ? "تماس‌های دفتر" : "تماس‌های شما"} در ${faNum(ov.days)} روز، به تفکیک روز و ساعت`}
    >
      {total === 0 ? (
        <Empty>در این بازه تماسی ثبت نشده است.</Empty>
      ) : (
        <>
          <CallHeatmap grid={rows} />
          <p className="mt-2 text-xs leading-6 text-muted-foreground">
            بیشترین تماس: <span className="font-semibold text-foreground">{DAY_NAMES[best.d]}، ساعت {faNum(ov.call_grid.hours[best.h])}</span>
            {" "}({faNum(best.v)} تماس از {faNum(total)}).
          </p>
        </>
      )}
    </Panel>
  );
}

function dealMonths(ov: Overview): DealMonth[] {
  const byMonth = new Map<string, DealMonth>();
  const label = (iso: string) => faDate(day(iso), { month: "long" });
  // the last six Jalali months, oldest first, even the empty ones
  const cursor = new Date();
  while (byMonth.size < 6) {
    // a step shorter than any month, so none is skipped
    const m = label(cursor.toISOString().slice(0, 10));
    if (!byMonth.has(m)) byMonth.set(m, { month: m, buy: 0, rent: 0, lease: 0 });
    cursor.setDate(cursor.getDate() - 10);
  }
  for (const d of ov.deals_by_month) {
    const row = byMonth.get(label(d.date));
    if (!row) continue;
    const k = d.type === "rent" || d.type === "lease" ? d.type : "buy";
    row[k] += d.count;
  }
  return [...byMonth.values()].reverse();
}

function DealsPanel({ ov }: { ov: Overview }) {
  const data = dealMonths(ov);
  const any = data.some((d) => d.buy + d.rent + d.lease > 0);
  return (
    <Panel title="قراردادها به تفکیک نوع" hint="۶ ماه گذشته" action={<MoreLink href={legacy("crm")}>معاملات</MoreLink>}>
      {any ? <DealsByMonth data={data} /> : <Empty>در ۶ ماه گذشته قراردادی ثبت نشده است.</Empty>}
    </Panel>
  );
}

/** The radar compares people, so it needs at least two who did something. */
const active = (ov: Overview) => ov.team.filter((t) => t.calls + t.visits + t.won > 0);

function RadarPanel({ ov }: { ov: Overview }) {
  const team = active(ov);
  const metrics = [
    { metric: "تماس", get: (t: Overview["team"][number]) => t.calls },
    { metric: "پاسخ‌گیری", get: (t: Overview["team"][number]) => t.answer_rate ?? 0 },
    { metric: "بازدید", get: (t: Overview["team"][number]) => t.visits },
    { metric: "قرارداد", get: (t: Overview["team"][number]) => t.won },
  ];
  const best = team[0];
  const data = metrics.map((m) => {
    const max = Math.max(1, ...team.map(m.get));
    const avg = team.reduce((a, t) => a + m.get(t), 0) / team.length;
    return { metric: m.metric, best: Math.round((m.get(best) / max) * 100), avg: Math.round((avg / max) * 100) };
  });
  return (
    <Panel title="مقایسهٔ عملکرد" hint={`بهترین مشاور در برابر میانگین تیم، از ۱۰۰ (${faNum(ov.days)} روز)`}>
      <TeamRadar data={data} bestLabel={best.name} />
    </Panel>
  );
}

const PRESENCE = { available: "bg-success", busy: "bg-warning", away: "bg-muted-foreground" } as const;
const ROLE_FA: Record<string, string> = { root: "Root", super_admin: "مدیر ارشد", admin: "مشاور" };

function TeamPanel({ ov }: { ov: Overview }) {
  return (
    <Panel
      title={ov.scope === "office" ? "عملکرد تیم" : "عملکرد شما"}
      hint={`${faNum(ov.days)} روز گذشته`}
      bodyClassName="px-0 pb-2"
    >
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="ps-5">مشاور</TableHead>
              <TableHead className="text-center">تماس</TableHead>
              <TableHead className="text-center">پاسخ‌گیری</TableHead>
              <TableHead className="text-center">بازدید</TableHead>
              <TableHead className="pe-5 text-center">قرارداد</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {ov.team.map((t) => (
              <TableRow key={t.id}>
                <TableCell className="ps-5">
                  <div className="flex items-center gap-2.5">
                    <div className="relative">
                      <Avatar className="size-8">
                        <AvatarFallback className="text-xs font-semibold">{initials(t.name)}</AvatarFallback>
                      </Avatar>
                      <span className={cn("absolute -bottom-0.5 -end-0.5 size-2.5 rounded-full ring-2 ring-card", PRESENCE[t.presence] ?? PRESENCE.available)} />
                    </div>
                    <div className="leading-tight">
                      <div className="text-sm font-semibold">{t.name}</div>
                      <div className="text-[11px] text-muted-foreground">{ROLE_FA[t.role] ?? t.role}</div>
                    </div>
                  </div>
                </TableCell>
                <TableCell className="text-center tabular">{faNum(t.calls)}</TableCell>
                <TableCell className="text-center tabular">
                  {t.answer_rate === null ? "—" : (
                    <span className={cn(t.answer_rate < 40 && "text-warning")}>{faPercent(t.answer_rate, 0)}</span>
                  )}
                </TableCell>
                <TableCell className="text-center tabular">{faNum(t.visits)}</TableCell>
                <TableCell className="pe-5 text-center font-bold tabular">{faNum(t.won)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </Panel>
  );
}

/* ───────────────────────── system & activity ───────────────────────── */

const healthy = (v?: string) => !!v && /^(healthy|ready|valid)/.test(v);

function SystemPanel({ health, jobs }: { health: ReturnType<typeof useSide<Health>>; jobs: ReturnType<typeof useSide<JobsSummary>> }) {
  const h = health.data;
  const running = jobs.data?.by_status?.running ?? 0;
  const lastJob = jobs.data?.recent_jobs?.[0];
  const cookie = h?.cookie_status ?? "";
  const tiles = [
    {
      Icon: Database, title: "پایگاه داده", ok: healthy(h?.database),
      value: healthy(h?.database) ? "سالم" : "مشکل دارد", sub: healthy(h?.database) ? "" : h?.database,
    },
    {
      Icon: Server, title: "Redis", ok: healthy(h?.redis),
      value: healthy(h?.redis) ? "سالم" : "مشکل دارد", sub: healthy(h?.redis) ? "" : h?.redis,
    },
    {
      Icon: Radar, title: "اسکرپر", ok: healthy(h?.scraper),
      value: running ? `${faNum(running)} اسکرپ در حال اجرا` : healthy(h?.scraper) ? "آماده" : "در دسترس نیست",
      sub: lastJob ? `آخرین اجرا ${ago(lastJob.completed_at ?? lastJob.started_at ?? lastJob.created_at)}` : "",
      progress: running && lastJob?.progress != null ? lastJob.progress : undefined,
    },
    {
      Icon: KeyRound, title: "کوکی دیوار شما", ok: cookie.startsWith("valid"),
      value: cookie.startsWith("valid") ? "معتبر" : cookie === "expired" ? "منقضی شده" : "ثبت نشده",
      sub: cookie.match(/\((\d+) days left\)/) ? `${faNum(Number(cookie.match(/\((\d+) days/)![1]))} روز مانده` : "",
    },
  ];
  return (
    <Panel
      title="وضعیت سامانه"
      hint={health.dataUpdatedAt ? `به‌روز شده ${ago(new Date(health.dataUpdatedAt).toISOString())}` : undefined}
      action={
        <Button
          variant="ghost" size="icon" className="size-7" aria-label="به‌روزرسانی وضعیت"
          onClick={() => { void health.refetch(); void jobs.refetch(); }}
        >
          <RefreshCw className={cn("size-3.5", health.isFetching && "animate-spin")} />
        </Button>
      }
    >
      {health.isPending ? (
        <ListSkeleton />
      ) : health.isError ? (
        <PanelError error={health.error} />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {tiles.map((t) => (
            <div key={t.title} className="flex gap-3 rounded-xl border p-3">
              <div className={cn("grid size-9 shrink-0 place-items-center rounded-lg", t.ok ? "text-success bg-success/12" : "text-warning bg-warning/15")}>
                <t.Icon className="size-[18px]" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-xs text-muted-foreground">{t.title}</div>
                <div className={cn("text-sm font-bold", !t.ok && "text-warning")}>{t.value}</div>
                {t.sub && <div className="truncate text-[11px] text-muted-foreground">{t.sub}</div>}
                {t.progress !== undefined && <Progress value={t.progress} className="mt-2 h-1" />}
              </div>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

const LEAD_STATUS: Record<string, string> = {
  new: "تازه", contacted: "تماس گرفته‌شده", qualified: "آمادهٔ بازدید", visit: "بازدید", contract_meeting: "جلسهٔ قرارداد",
  closed: "قرارداد بسته شد", rented: "اجاره رفت", rejected: "رد شد",
};

function LatestPanel({ props, leads }: { props: ReturnType<typeof useSide<PropertyPage>>; leads: ReturnType<typeof useSide<LeadPage>> }) {
  const cols = [
    props.fetchStatus !== "idle" || props.data ? {
      key: "props", title: "آخرین املاک اسکرپ‌شده", Icon: Bot, q: props, href: legacy("properties"),
      rows: (props.data?.items ?? []).map((p) => ({
        id: p.id, main: p.title || "بدون عنوان", sub: [p.city_name, p.district].filter(Boolean).join("، "),
        when: ago(p.scraped_at ?? p.created_at),
      })),
    } : null,
    leads.fetchStatus !== "idle" || leads.data ? {
      key: "leads", title: "آخرین لیدها", Icon: Users, q: leads, href: legacy("crm"),
      rows: (leads.data?.items ?? []).map((l) => ({
        id: l.id, main: l.seller_name?.trim() || l.property_title || "بدون نام",
        sub: [LEAD_STATUS[l.status] ?? l.status, l.assigned_to].filter(Boolean).join(" · "), when: ago(l.created_at),
      })),
    } : null,
  ].filter((c): c is NonNullable<typeof c> => c !== null);
  if (!cols.length) return null;
  return (
    <div className={cn("grid grid-cols-1 gap-5", cols.length === 2 && "lg:grid-cols-2")}>
      {cols.map((c) => (
        <Panel key={c.key} title={c.title} action={<MoreLink href={c.href} />}>
          {c.q.isPending ? (
            <ListSkeleton />
          ) : c.q.isError ? (
            <PanelError error={c.q.error} />
          ) : !c.rows.length ? (
            <Empty>چیزی ثبت نشده است.</Empty>
          ) : (
            <ul className="flex flex-col gap-3.5">
              {c.rows.map((r) => (
                <li key={r.id} className="flex gap-3">
                  <div className="grid size-8 shrink-0 place-items-center rounded-full bg-muted text-muted-foreground">
                    <c.Icon className="size-4" />
                  </div>
                  <div className="min-w-0 flex-1 text-sm leading-6">
                    <div className="truncate font-semibold">{r.main}</div>
                    <div className="truncate text-[11px] text-muted-foreground">{[r.sub, r.when].filter(Boolean).join(" — ")}</div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      ))}
    </div>
  );
}

function ListSkeleton() {
  return (
    <div className="flex flex-col gap-3" aria-hidden>
      {[0, 1, 2].map((i) => (
        <div key={i} className="flex items-center gap-3">
          <Skeleton className="size-9 rounded-full" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-3 w-2/3" />
            <Skeleton className="h-3 w-1/3" />
          </div>
        </div>
      ))}
    </div>
  );
}

function PageSkeleton() {
  return (
    <div className="flex flex-col gap-5" aria-busy="true" aria-label="در حال بارگیری داشبورد">
      <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-3 2xl:grid-cols-6">
        {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-36 rounded-2xl" />)}
      </div>
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-12">
        <Skeleton className="h-80 rounded-2xl xl:col-span-8" />
        <Skeleton className="h-80 rounded-2xl xl:col-span-4" />
      </div>
    </div>
  );
}

/* ───────────────────────── page ───────────────────────── */

function Row({ className, children }: { className?: string; children: React.ReactNode }) {
  // grid-cols-1 is minmax(0,1fr): a wide child (the heatmap) scrolls inside
  // its panel instead of widening the whole page on a phone.
  return <div className={cn("grid grid-cols-1 gap-5", className)}>{children}</div>;
}

export function Dashboard() {
  const user = useSession().data?.user;
  const [days, setDays] = useState(30);
  const stats = can(user, { perm: "stats" });
  const crm = can(user, { perm: "crm" });
  const properties = can(user, { perm: "properties" });

  const ov = useOverview(days, stats);
  const health = useSide<Health>("health", "/stats/health", stats);
  const jobs = useSide<JobsSummary>("jobs", "/stats/jobs-summary", stats);
  const calls = useSide<CallsToday>("calls", "/crm/calls/today?limit=6", crm);
  const matches = useSide<Matches>("matches", "/crm/matches?limit=3", crm);
  const upcoming = useSide<Upcoming>("upcoming", "/crm/calendar/upcoming?days=7&limit=8", crm);
  const latestProps = useSide<PropertyPage>("props", "/properties?page=1&size=5", properties);
  const latestLeads = useSide<LeadPage>("leads", "/crm/leads?limit=5", crm);

  if (!user) return <PageSkeleton />;

  return (
    <div className="mx-auto flex max-w-[1480px] flex-col gap-5">
      <Reveal>
        <Greeting user={user} ov={ov.data} upcoming={upcoming.data} />
      </Reveal>

      {!stats ? (
        <Empty>دسترسی «آمار» برای حساب شما فعال نیست؛ عددهای دفتر اینجا نشان داده نمی‌شوند.</Empty>
      ) : ov.isPending ? (
        <PageSkeleton />
      ) : ov.isError ? (
        <PanelError error={ov.error} />
      ) : (
        <>
          <Kpis ov={ov.data} />
          <Row className="xl:grid-cols-12">
            <Reveal className="xl:col-span-8"><TrendPanel ov={ov.data} days={days} onDays={setDays} /></Reveal>
            <Reveal className="xl:col-span-4" delay={0.08}><SourcesPanel ov={ov.data} /></Reveal>
          </Row>
          <Row className="xl:grid-cols-12">
            <Reveal className="xl:col-span-8"><SkylinePanel ov={ov.data} /></Reveal>
            <Reveal className="xl:col-span-4" delay={0.08}><TargetPanel ov={ov.data} /></Reveal>
          </Row>
        </>
      )}

      {crm && (
        <Row className="lg:grid-cols-2 xl:grid-cols-12">
          <Reveal className="xl:col-span-5"><CallsPanel q={calls} /></Reveal>
          <Reveal className="xl:col-span-4" delay={0.06}><MatchesPanel q={matches} /></Reveal>
          <Reveal className="lg:col-span-2 xl:col-span-3" delay={0.12}><AgendaPanel q={upcoming} /></Reveal>
        </Row>
      )}

      {stats && ov.data && (
        <>
          <Row className="xl:grid-cols-12">
            <Reveal className="xl:col-span-7"><HeatmapPanel ov={ov.data} /></Reveal>
            <Reveal className="xl:col-span-5" delay={0.08}><FunnelPanel ov={ov.data} /></Reveal>
          </Row>
          <Row className="xl:grid-cols-12">
            <Reveal className={active(ov.data).length >= 2 ? "xl:col-span-7" : "xl:col-span-12"}>
              <TeamPanel ov={ov.data} />
            </Reveal>
            {active(ov.data).length >= 2 && (
              <Reveal className="xl:col-span-5" delay={0.08}><RadarPanel ov={ov.data} /></Reveal>
            )}
          </Row>
          <Row className="xl:grid-cols-12">
            <Reveal className="xl:col-span-6"><DealsPanel ov={ov.data} /></Reveal>
            <Reveal className="xl:col-span-6" delay={0.08}><SystemPanel health={health} jobs={jobs} /></Reveal>
          </Row>
        </>
      )}

      <Reveal><LatestPanel props={latestProps} leads={latestLeads} /></Reveal>

      {ov.data && (
        <p className="flex items-center justify-center gap-1.5 pb-2 text-center text-xs text-muted-foreground">
          <CalendarDays className="size-3.5" />
          به‌روز شده {ago(ov.data.generated_at)} · هر دقیقه تازه می‌شود
        </p>
      )}
    </div>
  );
}
