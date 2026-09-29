"use client";

// What the phones sent in the last day, and what became of it — drawn from
// the very events the log below lists (GET /sms/events?stage=inbound), never
// from a number of its own. It counts only what the API returned: when the
// last 100 events do not reach back a full day the chart starts where they
// do, instead of drawing empty hours the log simply never saw.

import { useMemo } from "react";
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { Reveal } from "@/components/viz";
import { faNum, faPercent } from "@/lib/format";
import { bucketOf } from "./shared";
import type { SmsEvent } from "./types";

const HOUR = 3_600_000;

const config = {
  matched: { label: "به اسکرپر داده شد", color: "var(--success)" },
  parked_early: { label: "زودتر رسید", color: "var(--info)" },
  problem: { label: "مشکل‌دار", color: "var(--warning)" },
} satisfies ChartConfig;

type Row = { t: number; hour: string; matched: number; parked_early: number; problem: number };

/** Same validity window the log's own latency column uses: a handset with a
 *  wrong clock reports negative or absurd times, and those are not data. */
function goodLatency(ms: unknown): number | null {
  return typeof ms === "number" && Number.isFinite(ms) && ms >= 0 && ms <= 5 * 60_000 ? ms : null;
}

function median(xs: number[]): number | null {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

function summarize(events: SmsEvent[], now: number, full: boolean) {
  const stamped = events.flatMap((e) => {
    const t = e.at ? Date.parse(e.at) : NaN;
    return Number.isFinite(t) ? [{ e, t }] : [];
  });
  if (!stamped.length) return null;
  const end = Math.floor(now / HOUR) * HOUR;
  let start = end - 23 * HOUR;
  if (full) {
    const oldest = Math.min(...stamped.map((s) => s.t));
    if (oldest > start) start = Math.floor(oldest / HOUR) * HOUR;
  }
  const rows: Row[] = [];
  for (let t = start; t <= end; t += HOUR) {
    rows.push({ t, hour: faNum(new Date(t).getHours(), { useGrouping: false }), matched: 0, parked_early: 0, problem: 0 });
  }
  let inWindow = 0;
  const lat: number[] = [];
  for (const { e, t } of stamped) {
    if (t < start) continue;
    const row = rows[Math.min(rows.length - 1, Math.floor((t - start) / HOUR))];
    const b = bucketOf(e.details.reason);
    if (b === "matched") row.matched += 1;
    else if (b === "parked_early") row.parked_early += 1;
    else row.problem += 1;
    inWindow += 1;
    if (b === "matched") {
      const l = goodLatency(e.details.latency_ms);
      if (l !== null) lat.push(l);
    }
  }
  if (!inWindow) return null;
  const matched = rows.reduce((a, r) => a + r.matched, 0);
  return { rows, inWindow, matched, latencyMs: median(lat), hours: rows.length };
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border bg-muted/20 px-3 py-2.5">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="mt-0.5 text-lg leading-tight font-black tabular">{value}</div>
      {hint && <div className="text-[11px] text-muted-foreground">{hint}</div>}
    </div>
  );
}

export function CodesChart({ events, asOf, full }: { events: SmsEvent[]; asOf: number; full: boolean }) {
  const s = useMemo(() => summarize(events, asOf, full), [events, asOf, full]);
  if (!s) return null;
  const rate = s.inWindow ? (s.matched / s.inWindow) * 100 : 0;
  return (
    <Reveal className="mb-4 grid gap-3 lg:grid-cols-[minmax(0,1fr)_200px]">
      <div className="min-w-0 rounded-xl border bg-muted/10 p-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground">
          <span>کدهای رسیده در هر ساعت{s.hours < 24 ? ` (${faNum(s.hours)} ساعت اخیر)` : " (۲۴ ساعت اخیر)"}</span>
          <span className="flex flex-wrap items-center gap-3">
            {Object.entries(config).map(([k, c]) => (
              <span key={k} className="flex items-center gap-1.5">
                <span className="size-2 rounded-sm" style={{ background: c.color }} />
                {c.label}
              </span>
            ))}
          </span>
        </div>
        <ChartContainer config={config} className="aspect-auto h-[150px] w-full">
          <BarChart data={s.rows} margin={{ top: 4, left: 0, right: 0, bottom: 0 }} barCategoryGap="18%">
            <CartesianGrid vertical={false} strokeDasharray="3 3" />
            <XAxis dataKey="hour" reversed tickLine={false} axisLine={false} tickMargin={6} interval="preserveStartEnd" minTickGap={12} />
            <YAxis orientation="right" tickLine={false} axisLine={false} width={24} allowDecimals={false} tickFormatter={(v: number) => faNum(v)} />
            <ChartTooltip cursor={{ fill: "var(--muted)", opacity: 0.5 }} content={<ChartTooltipContent indicator="dot" labelFormatter={(l) => `ساعت ${l}`} />} />
            <Bar dataKey="matched" stackId="c" fill="var(--color-matched)" isAnimationActive={false} />
            <Bar dataKey="parked_early" stackId="c" fill="var(--color-parked_early)" isAnimationActive={false} />
            <Bar dataKey="problem" stackId="c" fill="var(--color-problem)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
          </BarChart>
        </ChartContainer>
      </div>
      <div className="grid grid-cols-3 gap-2 lg:grid-cols-1">
        <Stat label="کد در این بازه" value={faNum(s.inWindow)} hint={full ? "از ۱۰۰ رویداد آخر" : undefined} />
        <Stat label="به اسکرپر رسید" value={faPercent(rate, 0)} />
        <Stat label="میانهٔ تأخیر" value={s.latencyMs === null ? "—" : `${faNum(Math.round(s.latencyMs / 1000))} ثانیه`} hint="از رسیدن پیامک تا سرور" />
      </div>
    </Reveal>
  );
}
