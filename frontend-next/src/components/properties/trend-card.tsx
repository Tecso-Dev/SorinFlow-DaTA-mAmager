"use client";

// How many listings the scraper has brought in each day for the last month,
// and how many of those came with a phone number — GET /stats/property-trends,
// the same endpoint the dashboard's own numbers come from. It is about the
// whole database, not about the filters below it, and it says so. The route
// sits behind the `stats` permission while this page sits behind `properties`,
// so someone with only the second simply does not get the card; a failed or
// empty answer draws nothing rather than an error over what is a bonus.

import { useQuery } from "@tanstack/react-query";
import { useId, useMemo } from "react";
import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { api } from "@/lib/api";
import { faDate, faNum, faPercent } from "@/lib/format";
import { can, useSession } from "@/lib/session";

type Trends = { trends: { date: string; total: number; with_phone: number }[] };

const DAYS = 30;
const config = {
  total: { label: "آگهی تازه", color: "var(--chart-1)" },
  with_phone: { label: "با شمارهٔ تماس", color: "var(--chart-3)" },
} satisfies ChartConfig;

// «2026-09-01» is a calendar day, not an instant: pin it to local noon so no
// time zone can move it to the day before
const day = (iso: string) => new Date(`${iso}T12:00:00`);

export function TrendCard() {
  const user = useSession().data?.user;
  const allowed = can(user, { perm: "stats" });
  const uid = `pt${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const q = useQuery({
    queryKey: ["stats", "property-trends", DAYS],
    queryFn: () => api<Trends>(`/stats/property-trends?days=${DAYS}`),
    enabled: allowed,
    retry: false,
    staleTime: 5 * 60_000,
  });

  const rows = useMemo(() => q.data?.trends ?? [], [q.data]);
  const sum = useMemo(() => rows.reduce((a, r) => ({ total: a.total + r.total, phone: a.phone + r.with_phone }), { total: 0, phone: 0 }), [rows]);

  if (!allowed || q.isError) return null;
  if (q.isPending) return <div aria-busy="true" aria-label="در حال بارگیری" className="h-[148px] animate-pulse rounded-2xl bg-muted/40" />;
  if (!sum.total) return null;

  return (
    <div className="min-w-0 rounded-2xl border bg-card p-4 shadow-sm dark:bg-[#131320] dark:shadow-none">
      <div className="mb-2 flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
        <div>
          <div className="text-[13px] font-bold">آگهی‌های اسکرپ‌شده در {faNum(DAYS)} روز اخیر</div>
          <div className="text-[11px] text-muted-foreground">کل پایگاه، مستقل از فیلترهای پایین</div>
        </div>
        <div className="flex items-center gap-4 text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="size-2 rounded-full" style={{ background: config.total.color }} />
            {config.total.label}: <b className="font-bold text-foreground tabular">{faNum(sum.total)}</b>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="size-2 rounded-full" style={{ background: config.with_phone.color }} />
            {config.with_phone.label}: <b className="font-bold text-foreground tabular">{faPercent((sum.phone / sum.total) * 100, 0)}</b>
          </span>
        </div>
      </div>
      <ChartContainer config={config} className="aspect-auto h-[104px] w-full">
        <AreaChart data={rows} margin={{ top: 4, left: 0, right: 0, bottom: 0 }}>
          <defs>
            {(["total", "with_phone"] as const).map((k) => (
              <linearGradient key={k} id={`${uid}${k}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={`var(--color-${k})`} stopOpacity={k === "total" ? 0.4 : 0.3} />
                <stop offset="100%" stopColor={`var(--color-${k})`} stopOpacity={0.02} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid vertical={false} strokeDasharray="3 3" />
          <XAxis dataKey="date" reversed tickLine={false} axisLine={false} tickMargin={6} minTickGap={36} tickFormatter={(d: string) => faDate(day(d), { month: "short", day: "numeric" })} />
          <YAxis orientation="right" tickLine={false} axisLine={false} width={28} allowDecimals={false} tickFormatter={(v: number) => faNum(v)} />
          <ChartTooltip content={<ChartTooltipContent indicator="dot" labelFormatter={(d) => faDate(day(String(d)), { dateStyle: "medium" })} />} />
          <Area dataKey="total" type="monotone" stroke="var(--color-total)" strokeWidth={2} fill={`url(#${uid}total)`} isAnimationActive={false} />
          <Area dataKey="with_phone" type="monotone" stroke="var(--color-with_phone)" strokeWidth={2} fill={`url(#${uid}with_phone)`} isAnimationActive={false} />
        </AreaChart>
      </ChartContainer>
    </div>
  );
}
