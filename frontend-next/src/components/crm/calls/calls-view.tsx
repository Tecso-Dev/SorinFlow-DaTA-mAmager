"use client";

// «تماس‌های امروز»: the office's most used screen. The call queue on the
// wide side, the match engine and the price watcher beside it, the day in
// four numbers on top, and for root / super_admin the calls per agent.

import { CalendarCheck, Crosshair, PhoneCall, RotateCcw, TrendingDown, Users } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { cn } from "cn";
import { PageHeader, Section } from "@/components/panel/kit";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { CountUp, Reveal, Tilt } from "@/components/viz";
import { api } from "@/lib/api";
import { faNum } from "@/lib/format";
import { LeadSheet } from "../leads/lead-sheet";
import { useIsSuper } from "../leads/shared";
import { CallQueue, type CallsToday } from "./call-queue";
import { DropsCard, type DropsSummary, type PriceDrop } from "./drops-card";
import { MatchesCard, type MatchesSummary, type QueueMatch } from "./matches-card";

type Summary = {
  days: number;
  agents: { agent: string; calls: number; answered: number; no_answer: number; visit: number; callback: number; rejected: number }[];
  unassigned_due: number;
};

export function CallsView() {
  const isSuper = useIsSuper();
  const [lead, setLead] = useState<number | null>(null);
  const calls = useQuery({
    queryKey: ["crm", "calls", "today"],
    queryFn: () => api<CallsToday>("/crm/calls/today?limit=40"),
    refetchInterval: 60_000,
  });
  const matches = useQuery({
    queryKey: ["crm", "calls", "matches"],
    queryFn: () => api<{ items: QueueMatch[]; total: number }>("/crm/matches?status=new&limit=40"),
    refetchInterval: 60_000,
  });
  const mSummary = useQuery({
    queryKey: ["crm", "calls", "matches-summary"],
    queryFn: () => api<MatchesSummary>("/crm/matches/summary"),
  });
  const drops = useQuery({
    queryKey: ["crm", "calls", "drops"],
    queryFn: () => api<{ items: PriceDrop[]; total: number }>("/crm/price-drops?status=new&limit=40"),
    refetchInterval: 120_000,
  });
  const dSummary = useQuery({
    queryKey: ["crm", "calls", "drops-summary"],
    queryFn: () => api<DropsSummary>("/crm/price-drops/summary"),
  });
  const summary = useQuery({
    queryKey: ["crm", "calls", "summary"],
    queryFn: () => api<Summary>("/crm/calls/summary?days=1"),
    enabled: isSuper,
  });

  const c = calls.data;
  const tiles = [
    { label: "در صف تماس", value: c?.total, icon: PhoneCall, tint: "text-chart-1 bg-chart-1/12" },
    { label: "تماس امروز شما", value: c?.done_today, icon: CalendarCheck, tint: "text-chart-2 bg-chart-2/12" },
    { label: "تماس مجدد سررسیده", value: c?.due_callbacks, icon: RotateCcw, tint: "text-chart-3 bg-chart-3/12" },
    { label: "تطبیق تازه", value: matches.data?.total, icon: Crosshair, tint: "text-chart-4 bg-chart-4/12" },
    { label: "ارزان شدند", value: drops.data?.total, icon: TrendingDown, tint: "text-chart-5 bg-chart-5/12" },
  ];

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        icon={PhoneCall}
        title="تماس‌های امروز"
        hint="نوبت تماس‌ها، مشتری‌های هم‌خوان و ملک‌هایی که ارزان شدند"
      />

      <div className="-mx-4 flex snap-x gap-2.5 overflow-x-auto px-4 pb-1 [scrollbar-width:none] sm:mx-0 sm:grid sm:grid-cols-3 sm:gap-3 sm:overflow-visible sm:px-0 lg:grid-cols-5">
        {tiles.map((t, i) => (
          <Reveal key={t.label} delay={i * 0.05} className="min-w-[9.5rem] shrink-0 snap-start sm:min-w-0">
            <Tilt className="h-full rounded-2xl">
              <div className="flex h-full items-center gap-2.5 rounded-2xl border bg-card p-3 sm:gap-3 sm:p-3.5 shadow-sm dark:bg-linear-to-b dark:from-white/[0.035] dark:to-white/[0.008] dark:shadow-none">
                <span className={cn("grid size-9 shrink-0 place-items-center rounded-xl sm:size-10", t.tint)}>
                  <t.icon className="size-5" aria-hidden />
                </span>
                <div className="min-w-0">
                  <div className="text-xl font-black leading-none sm:text-2xl">{t.value === undefined ? "—" : <CountUp value={t.value} />}</div>
                  <div className="mt-1 truncate text-xs text-muted-foreground">{t.label}</div>
                </div>
              </div>
            </Tilt>
          </Reveal>
        ))}
      </div>

      <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
        <div className="grid min-w-0 gap-5">
          <CallQueue q={calls} onOpenLead={setLead} />
          {isSuper && <AgentSummary data={summary.data} />}
        </div>
        <div className="grid min-w-0 gap-5">
          <MatchesCard list={matches} summary={mSummary.data} />
          <DropsCard list={drops} summary={dSummary.data} />
        </div>
      </div>

      <LeadSheet id={lead} onClose={() => setLead(null)} />
    </div>
  );
}

const COLS = [
  { key: "answered", label: "پاسخ داد", color: "bg-success" },
  { key: "no_answer", label: "پاسخ نداد", color: "bg-muted-foreground/50" },
  { key: "callback", label: "تماس مجدد", color: "bg-warning" },
  { key: "visit", label: "بازدید", color: "bg-primary" },
  { key: "rejected", label: "بسته شد", color: "bg-destructive" },
] as const;

function AgentSummary({ data }: { data?: Summary }) {
  if (!data) return null;
  return (
    <Section
      title={<span className="flex items-center gap-1.5"><Users className="size-4 text-primary" aria-hidden /> امروز، به تفکیک مشاور</span>}
      hint={`${faNum(data.unassigned_due)} لید بی‌مسئول در صف`}
    >
      {!data.agents.length ? (
        <p className="text-sm text-muted-foreground">امروز هنوز تماسی ثبت نشده است.</p>
      ) : (
        <Table className="text-[13px]">
          <TableHeader>
            <TableRow>
              <TableHead>مشاور</TableHead>
              <TableHead>تماس</TableHead>
              {COLS.map((c) => <TableHead key={c.key} className="hidden sm:table-cell">{c.label}</TableHead>)}
              <TableHead className="min-w-32">ترکیب</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.agents.map((a) => (
              <TableRow key={a.agent}>
                <TableCell className="font-semibold">{a.agent}</TableCell>
                <TableCell className="tabular">{faNum(a.calls)}</TableCell>
                {COLS.map((c) => <TableCell key={c.key} className="hidden tabular sm:table-cell">{faNum(a[c.key])}</TableCell>)}
                <TableCell>
                  <div
                    className="flex h-2 w-full overflow-hidden rounded-full bg-muted"
                    role="img"
                    aria-label={COLS.map((c) => `${c.label} ${faNum(a[c.key])}`).join("، ")}
                  >
                    {COLS.map((c) => (
                      <div key={c.key} className={c.color} style={{ width: `${a.calls ? (a[c.key] / a.calls) * 100 : 0}%` }} />
                    ))}
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <div className="mt-3 flex flex-wrap gap-3 text-[11px] text-muted-foreground">
        {COLS.map((c) => (
          <span key={c.key} className="flex items-center gap-1"><span className={cn("size-2 rounded-full", c.color)} aria-hidden />{c.label}</span>
        ))}
      </div>
    </Section>
  );
}
