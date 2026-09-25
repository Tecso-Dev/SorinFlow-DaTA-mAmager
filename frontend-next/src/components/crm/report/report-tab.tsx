"use client";

// گزارش — /crm/stats as stat cards, the lead funnel, deals-by-status and
// contacts-by-type charts, with a 3D donut. Read-only.

import {
  Bell, CalendarCheck2, ChartPie, Handshake, MessageSquareText, Target, Users,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { CountUp, Donut3D, Reveal, Tilt } from "@/components/viz";
import { Empty, ErrorNote, ListSkeleton, PageHeader, Section } from "@/components/panel/kit";
import { api } from "@/lib/api";
import { CONTACT_TYPE, DEAL_STATUS, LEAD_STATUS } from "@/lib/crm";
import { faNum, faPercent, toman } from "@/lib/format";

type Stats = {
  leads: { total: number; by_status: Record<string, number>; notified: number; pending_notification: number };
  contacts: { total: number; by_type: Record<string, number> };
  tasks: { total: number; todo: number; done: number; overdue: number };
  deals: { total: number; by_status: Record<string, number>; closed_amount: number };
  reminders_due_today: number;
  total_sms: number;
};

const QKEY = ["crm", "stats"] as const;

/* ───────────────────────── stat cards ───────────────────────── */

function StatCard({
  icon: Icon, label, value, hint, tint, delay = 0,
}: { icon: React.ComponentType<{ className?: string }>; label: string; value: number; hint?: string; tint: string; delay?: number }) {
  return (
    <Reveal delay={delay}>
      <Tilt className="flex h-full flex-col gap-3 rounded-2xl border bg-card p-4 shadow-sm dark:bg-linear-to-b dark:from-white/[0.04] dark:to-transparent dark:shadow-none">
        <div className={`grid size-10 shrink-0 place-items-center rounded-xl ring-1 ring-inset ring-current/20 shadow-[0_0_20px_-6px_currentColor] ${tint}`}>
          <Icon className="size-5" />
        </div>
        <div>
          <div className="text-[13px] text-muted-foreground">{label}</div>
          <div className="mt-1 text-[26px] leading-none font-black tracking-tight tabular"><CountUp value={value} /></div>
          {hint && <div className="mt-1.5 truncate text-[11px] text-muted-foreground">{hint}</div>}
        </div>
      </Tilt>
    </Reveal>
  );
}

/* ───────────────────────── funnel ───────────────────────── */

const FUNNEL_ORDER = ["new", "contacted", "qualified", "visit", "contract_meeting", "closed"];

function LeadFunnel({ byStatus }: { byStatus: Record<string, number> }) {
  const steps = FUNNEL_ORDER.map((key) => ({ key, label: LEAD_STATUS[key]?.label ?? key, count: byStatus[key] ?? 0 }));
  const top = Math.max(1, ...steps.map((s) => s.count));
  const any = steps.some((s) => s.count > 0);
  return (
    <Section title="قیف لیدها" hint="همهٔ لیدها، از تازه تا بسته‌شده">
      {!any ? (
        <Empty icon={Target}>هنوز لیدی ثبت نشده است.</Empty>
      ) : (
        <ol className="flex flex-col gap-3.5">
          {steps.map((s, i) => {
            const pct = (s.count / top) * 100;
            return (
              <li key={s.key} className="flex flex-col gap-1.5">
                <div className="flex items-baseline justify-between gap-2 text-sm">
                  <span className="font-medium">{s.label}</span>
                  <span className="font-bold tabular">{faNum(s.count)}</span>
                </div>
                <div className="h-2.5 overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-linear-to-l from-indigo-500 to-violet-500"
                    style={{ width: `${Math.max(pct, s.count ? 3 : 0)}%`, opacity: 1 - i * 0.1 }}
                  />
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </Section>
  );
}

/* ───────────────────────── deals by status (bar chart) ───────────────────────── */

const dealsConfig = { count: { label: "تعداد", color: "var(--chart-2)" } } satisfies ChartConfig;

function DealsByStatusChart({ byStatus }: { byStatus: Record<string, number> }) {
  const data = Object.keys(DEAL_STATUS)
    .map((key) => ({ key, label: DEAL_STATUS[key].label, count: byStatus[key] ?? 0 }))
    .filter((d) => d.count > 0 || byStatus[d.key] !== undefined);
  const any = data.some((d) => d.count > 0);
  return (
    <Section title="معاملات به تفکیک وضعیت">
      {!any ? (
        <Empty icon={Handshake}>هنوز معامله‌ای ثبت نشده است.</Empty>
      ) : (
        <ChartContainer config={dealsConfig} className="aspect-auto h-[220px] w-full">
          <BarChart data={data} margin={{ top: 4, left: 4, right: 4, bottom: 0 }} barSize={28}>
            <CartesianGrid vertical={false} strokeDasharray="3 3" />
            <XAxis dataKey="label" reversed tickLine={false} axisLine={false} tickMargin={8} />
            <YAxis orientation="right" tickLine={false} axisLine={false} width={28} allowDecimals={false} tickFormatter={(v: number) => faNum(v)} />
            <ChartTooltip cursor={{ fill: "var(--muted)", opacity: 0.5 }} content={<ChartTooltipContent indicator="dot" />} />
            <Bar dataKey="count" fill="var(--color-count)" radius={[6, 6, 0, 0]} />
          </BarChart>
        </ChartContainer>
      )}
    </Section>
  );
}

/* ───────────────────────── contacts by type (3D donut) ───────────────────────── */

const CONTACT_COLOR: Record<string, string> = {
  owner: "var(--chart-1)", landlord: "var(--chart-2)", tenant: "var(--chart-3)",
  seeker: "var(--chart-4)", builder: "var(--chart-5)", agency: "var(--destructive)",
};

function ContactsByType({ byType }: { byType: Record<string, number> }) {
  const data = Object.entries(byType)
    .filter(([, v]) => v > 0)
    .map(([key, value]) => ({ label: CONTACT_TYPE[key]?.label ?? key, value, color: CONTACT_COLOR[key] ?? "var(--chart-4)" }));
  return (
    <Section title="مخاطبان به تفکیک نوع" hint="دفترچهٔ تلفن دفتر">
      {data.length === 0 ? <Empty icon={Users}>هنوز مخاطبی ثبت نشده است.</Empty> : <Donut3D data={data} centerLabel="مخاطب" />}
    </Section>
  );
}

/* ───────────────────────── page ───────────────────────── */

export function ReportTab() {
  const q = useQuery({ queryKey: QKEY, queryFn: () => api<Stats>("/crm/stats") });

  return (
    <div className="flex flex-col gap-5">
      <PageHeader icon={ChartPie} title="گزارش" hint="نمای کلی دفتر — لیدها، مخاطبان، وظایف و معاملات" />

      {q.isPending ? (
        <ListSkeleton rows={6} />
      ) : q.isError ? (
        <ErrorNote error={q.error} />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-3 2xl:grid-cols-6">
            <StatCard icon={Target} label="کل لیدها" value={q.data.leads.total} hint={`${faNum(q.data.leads.notified)} اطلاع داده شده`} tint="text-chart-1 bg-chart-1/12" />
            <StatCard icon={Users} label="مخاطبان" value={q.data.contacts.total} tint="text-chart-2 bg-chart-2/12" delay={0.04} />
            <StatCard
              icon={CalendarCheck2} label="وظایف" value={q.data.tasks.total}
              hint={q.data.tasks.overdue ? `${faNum(q.data.tasks.overdue)} عقب‌افتاده` : `${faNum(q.data.tasks.done)} انجام‌شده`}
              tint="text-chart-4 bg-chart-4/12" delay={0.08}
            />
            <StatCard
              icon={Handshake} label="معاملات" value={q.data.deals.total}
              hint={q.data.deals.closed_amount ? toman(q.data.deals.closed_amount) : "کمیسیونی ثبت نشده"}
              tint="text-chart-5 bg-chart-5/12" delay={0.12}
            />
            <StatCard icon={Bell} label="یادآور امروز" value={q.data.reminders_due_today} tint="text-warning bg-warning/12" delay={0.16} />
            <StatCard icon={MessageSquareText} label="پیامک ارسالی" value={q.data.total_sms} tint="text-primary bg-primary/12" delay={0.2} />
          </div>

          <div className="grid grid-cols-1 gap-5 xl:grid-cols-12">
            <Reveal className="xl:col-span-7"><LeadFunnel byStatus={q.data.leads.by_status} /></Reveal>
            <Reveal className="xl:col-span-5" delay={0.06}><ContactsByType byType={q.data.contacts.by_type} /></Reveal>
          </div>

          <Reveal delay={0.1}><DealsByStatusChart byStatus={q.data.deals.by_status} /></Reveal>

          <p className="text-center text-xs text-muted-foreground">
            {q.data.leads.pending_notification > 0
              ? `${faNum(q.data.leads.pending_notification)} لید هنوز به مشتری اطلاع داده نشده (${faPercent((q.data.leads.notified / Math.max(1, q.data.leads.total)) * 100, 0)} اطلاع‌رسانی شده).`
              : "همهٔ لیدها اطلاع‌رسانی شده‌اند."}
          </p>
        </>
      )}
    </div>
  );
}
