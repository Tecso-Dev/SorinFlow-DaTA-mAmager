"use client";

// «قیف و عملکرد» — GET /crm/insights?days=. One request for the whole tab,
// so every widget reads from the same moment. The funnel keeps whatever
// status the CRM has actually written, even one nobody defined a stage for
// — that is a data-quality signal, not noise to hide.

import { AlertTriangle, BarChart3, Clock, Handshake, MapPinned, Target, ThermometerSun, Timer, Users } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Cell, Line, LineChart, XAxis, YAxis,
} from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { Donut3D, Reveal, Tilt } from "@/components/viz";
import { Empty, ErrorNote, ListSkeleton, NativeSelect, Section, ToneBadge } from "@/components/panel/kit";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { TEMPERATURE } from "@/lib/crm";
import { faDate, faNum } from "@/lib/format";
import { insNum, insPct, insToman } from "./format";
import type { CrmInsights } from "./types";

const WINDOWS = [14, 30, 90] as const;
const STAT_TINT = ["text-chart-1 bg-chart-1/12", "text-success bg-success/12", "text-warning bg-warning/12", "text-chart-5 bg-chart-5/12"];
const TEMP_COLOR: Record<string, string> = { hot: "#fb7185", warm: "#fcd34d", cold: "#67e8f9" };
const CITY_PALETTE = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)", "var(--primary)"];

function StatCard({ icon: Icon, label, value, hint, tint, delay = 0 }: { icon: React.ComponentType<{ className?: string }>; label: string; value: string; hint?: string; tint: string; delay?: number }) {
  return (
    <Reveal delay={delay}>
      <Tilt className="flex h-full flex-col gap-3 rounded-2xl border bg-card p-4 shadow-sm dark:bg-linear-to-b dark:from-white/[0.04] dark:to-transparent dark:shadow-none">
        <div className={`grid size-10 shrink-0 place-items-center rounded-xl ring-1 ring-inset ring-current/20 shadow-[0_0_20px_-6px_currentColor] ${tint}`}>
          <Icon className="size-5" />
        </div>
        <div>
          <div className="text-[13px] text-muted-foreground">{label}</div>
          <div className="mt-1 text-[26px] leading-none font-black tracking-tight tabular">{value}</div>
          {hint && <div className="mt-1.5 truncate text-[11px] text-muted-foreground">{hint}</div>}
        </div>
      </Tilt>
    </Reveal>
  );
}

function Funnel({ stages }: { stages: CrmInsights["funnel"] }) {
  const top = Math.max(1, ...stages.map((s) => s.count));
  return (
    <ol className="flex flex-col gap-3.5">
      {stages.map((s, i) => {
        const pct = (s.count / top) * 100;
        return (
          <li key={s.key} className="flex flex-col gap-1.5">
            <div className="flex items-baseline justify-between gap-2 text-sm">
              <span className={`flex items-center gap-1 font-medium ${s.unexpected ? "text-warning" : ""}`} title={s.unexpected ? "وضعیت ناشناخته" : undefined}>
                {s.unexpected && <AlertTriangle className="size-3.5" aria-hidden />}
                {s.label}
              </span>
              <span className="font-bold tabular">{faNum(s.count)}</span>
            </div>
            <div className="h-2.5 overflow-hidden rounded-full bg-muted">
              <div
                className={`h-full rounded-full ${s.unexpected ? "bg-warning" : "bg-linear-to-l from-indigo-500 to-violet-500"}`}
                style={{ width: `${Math.max(pct, s.count ? 3 : 0)}%`, opacity: 1 - i * 0.06 }}
              />
            </div>
          </li>
        );
      })}
    </ol>
  );
}

const trendConfig = { leads: { label: "لید", color: "var(--chart-1)" }, properties: { label: "ملک", color: "var(--chart-3)" } } satisfies ChartConfig;

function TrendChart({ leads, properties }: { leads: { date: string; count: number }[]; properties: { date: string; count: number }[] }) {
  const data = leads.map((l, i) => ({
    date: l.date,
    label: faDate(new Date(l.date), { day: "numeric", month: "long" }),
    leads: l.count,
    properties: properties[i]?.count ?? 0,
  }));
  return (
    <ChartContainer config={trendConfig} className="aspect-auto h-[240px] w-full">
      <LineChart data={data} margin={{ top: 6, left: 4, right: 4, bottom: 0 }}>
        <CartesianGrid vertical={false} strokeDasharray="3 3" />
        <XAxis dataKey="label" reversed tickLine={false} axisLine={false} tickMargin={8} minTickGap={28} />
        <YAxis orientation="right" tickLine={false} axisLine={false} width={28} allowDecimals={false} tickFormatter={(v: number) => faNum(v)} />
        <ChartTooltip content={<ChartTooltipContent indicator="line" />} />
        <Line type="monotone" dataKey="leads" stroke="var(--color-leads)" strokeWidth={2} dot={false} />
        <Line type="monotone" dataKey="properties" stroke="var(--color-properties)" strokeWidth={2} dot={false} />
      </LineChart>
    </ChartContainer>
  );
}

const cityConfig = { count: { label: "آگهی", color: "var(--chart-2)" } } satisfies ChartConfig;

function CityChart({ cities }: { cities: CrmInsights["cities"] }) {
  const data = cities.map((c, i) => ({ label: c.label, count: c.count, fill: c.is_other ? "#64748b" : CITY_PALETTE[i % CITY_PALETTE.length] }));
  return (
    <ChartContainer config={cityConfig} className="aspect-auto h-[240px] w-full">
      <BarChart data={data} margin={{ top: 4, left: 4, right: 4, bottom: 0 }} barSize={28}>
        <CartesianGrid vertical={false} strokeDasharray="3 3" />
        <XAxis dataKey="label" reversed tickLine={false} axisLine={false} tickMargin={8} />
        <YAxis orientation="right" tickLine={false} axisLine={false} width={28} allowDecimals={false} tickFormatter={(v: number) => faNum(v)} />
        <ChartTooltip cursor={{ fill: "var(--muted)", opacity: 0.5 }} content={<ChartTooltipContent indicator="dot" />} />
        <Bar dataKey="count" radius={[6, 6, 0, 0]}>
          {data.map((d) => <Cell key={d.label} fill={d.fill} />)}
        </Bar>
      </BarChart>
    </ChartContainer>
  );
}

export function PipelineTab() {
  const [days, setDays] = useState<number>(30);
  const q = useQuery({ queryKey: ["crm", "insights", days], queryFn: () => api<CrmInsights>(`/crm/insights?days=${days}`) });

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center justify-end">
        <NativeSelect value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-36" aria-label="بازهٔ زمانی">
          {WINDOWS.map((w) => <option key={w} value={w}>{faNum(w)} روز اخیر</option>)}
        </NativeSelect>
      </div>

      {q.isLoading ? (
        <ListSkeleton rows={6} />
      ) : q.isError ? (
        <ErrorNote error={q.error} />
      ) : !q.data ? null : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
            <StatCard icon={Target} label="کل لیدها" value={insNum(q.data.totals.leads)} tint={STAT_TINT[0]} />
            <StatCard icon={Handshake} label="نرخ تبدیل به معامله" value={insPct(q.data.totals.conversion_rate)} tint={STAT_TINT[1]} delay={0.04} />
            <StatCard icon={Timer} label="لید بی‌پیگیری" value={insNum(q.data.stalled.items.length)} tint={STAT_TINT[2]} delay={0.08} />
            <StatCard icon={BarChart3} label="کمیسیون وصول‌نشده" value={insToman(q.data.deals.commission_due)} tint={STAT_TINT[3]} delay={0.12} />
          </div>

          <Reveal delay={0.1}>
            <p className="rounded-xl border bg-muted/30 px-4 py-2.5 text-sm leading-7 text-muted-foreground">
              {insPct(q.data.coverage.leads_with_phone)} لیدها شمارهٔ تماس دارند · {insPct(q.data.coverage.properties_with_phone)} املاک شمارهٔ تماس دارند
            </p>
          </Reveal>

          <div className="grid grid-cols-1 gap-5 xl:grid-cols-12">
            <Reveal className="xl:col-span-7" delay={0.06}>
              <Section title="قیف فروش" hint="از جدید تا موفق/از دست رفته، به همراه وضعیت‌های ناشناخته">
                <Funnel stages={q.data.funnel} />
              </Section>
            </Reveal>
            <Reveal className="xl:col-span-5" delay={0.1}>
              <Section title="دمای مشتریان">
                {!q.data.temperature.length ? (
                  <Empty icon={ThermometerSun}>هنوز مشتری‌ای ثبت نشده است.</Empty>
                ) : (
                  <Donut3D
                    data={q.data.temperature.map((t) => ({ label: TEMPERATURE[t.label]?.label ?? t.label, value: t.count, color: TEMP_COLOR[t.label] ?? "#64748b" }))}
                    centerLabel="مشتری"
                  />
                )}
              </Section>
            </Reveal>
          </div>

          <Reveal delay={0.14}>
            <Section title="روند لید و ملک" hint={`${faNum(days)} روز اخیر، شامل روزهای بدون رویداد`}>
              <TrendChart leads={q.data.series.leads} properties={q.data.series.properties} />
            </Section>
          </Reveal>

          <Reveal delay={0.18}>
            <Section title="پراکندگی شهرها">
              {!q.data.cities.length ? <Empty icon={MapPinned}>هنوز ملکی ثبت نشده است.</Empty> : <CityChart cities={q.data.cities} />}
            </Section>
          </Reveal>

          <Reveal delay={0.22}>
            <Section title="عملکرد مشاوران">
              {!q.data.agents.length ? (
                <Empty icon={Users}>هنوز عملکرد روزانه‌ای ثبت نشده است.</Empty>
              ) : (
                <div className="overflow-x-auto rounded-xl border">
                  <Table>
                    <TableHeader>
                      <TableRow><TableHead>مشاور</TableHead><TableHead>فایل نو</TableHead><TableHead>بازدید</TableHead><TableHead>پیشنهاد</TableHead><TableHead>معامله</TableHead><TableHead>بازدید/معامله</TableHead></TableRow>
                    </TableHeader>
                    <TableBody>
                      {q.data.agents.map((a) => (
                        <TableRow key={a.agent}>
                          <TableCell>
                            <div className="font-medium">{a.agent}</div>
                            <div className="text-[11px] text-muted-foreground">{faNum(a.days)} روز ثبت‌شده</div>
                          </TableCell>
                          <TableCell className="tabular">{faNum(a.new_files)}</TableCell>
                          <TableCell className="tabular">{faNum(a.showings)}</TableCell>
                          <TableCell className="tabular">{faNum(a.offers)}</TableCell>
                          <TableCell className="font-bold tabular">{faNum(a.closed)}</TableCell>
                          <TableCell className="tabular">{a.showings_per_close === null ? "—" : faNum(a.showings_per_close)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </Section>
          </Reveal>

          <Reveal delay={0.26}>
            <Section title="لیدهایی که معطل مانده‌اند" hint={`بیش از ${faNum(q.data.stalled.after_days)} روز`}>
              {!q.data.stalled.items.length ? (
                <Empty icon={Clock}>هیچ لید معطلی نیست.</Empty>
              ) : (
                <div className="overflow-x-auto rounded-xl border">
                  <Table>
                    <TableHeader>
                      <TableRow><TableHead>نام</TableHead><TableHead>شهر</TableHead><TableHead>وضعیت</TableHead><TableHead>بی‌حرکت</TableHead></TableRow>
                    </TableHeader>
                    <TableBody>
                      {q.data.stalled.items.map((s) => (
                        <TableRow key={s.id}>
                          <TableCell>{s.seller_name || s.phone_number || "—"}</TableCell>
                          <TableCell>{s.city_name || "—"}</TableCell>
                          <TableCell><ToneBadge tone="warning">{s.status_label}</ToneBadge></TableCell>
                          <TableCell className="tabular">{faNum(s.idle_days)} روز</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </Section>
          </Reveal>

          <p className="text-center text-xs text-muted-foreground">
            {insPct(q.data.coverage.leads_with_phone)} لیدها شمارهٔ تماس دارند · {insPct(q.data.coverage.properties_with_phone)} املاک شمارهٔ تماس دارند
          </p>
        </>
      )}
    </div>
  );
}
