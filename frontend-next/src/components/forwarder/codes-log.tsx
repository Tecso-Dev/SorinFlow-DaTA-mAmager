"use client";

// «کدهای رسیده از گوشی»: GET /sms/events?stage=inbound, gated by the `sms`
// permission at the router level (app/api/routes/__init__.py) — separate
// from `forwarder`, so a user can have one without the other. The parent
// hides this whole card rather than disabling it when that permission is
// missing; this component assumes it is allowed to be here.

import { ListFilter } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { useState } from "react";
import { cn } from "cn";
import { Empty, ErrorNote, ListSkeleton, Section, ToneBadge } from "@/components/panel/kit";
import { IsoAlert } from "@/components/panel/motion3d";
import { Lottie } from "@/components/ui/lottie";
import { api } from "@/lib/api";
import { faDate, faNum } from "@/lib/format";
import type { Tone } from "@/lib/crm";
import emptyLottie from "@/lotties/empty.json";
import { CodesChart } from "./codes-chart";
import { bucketOf, FW_REASON, type LogFilter } from "./shared";
import type { SmsEvent } from "./types";

const LIMIT = 100;

const FILTERS: { key: LogFilter; label: string }[] = [
  { key: "all", label: "همه" },
  { key: "matched", label: "به اسکرپر داده شد" },
  { key: "parked_early", label: "زودتر رسید — نگه داشته شد" },
  { key: "problem", label: "مشکل‌دار" },
];

const BUCKET_TONE: Record<LogFilter, Tone> = { all: "neutral", matched: "success", parked_early: "info", problem: "warning" };

// server latency_ms = server clock − the PHONE's own sentStamp (comment in
// app/api/routes/scraper.py): a wrong handset clock makes this negative or
// absurdly large, and showing that number instead of the truth is worse
// than showing nothing.
function latencyLabel(ms: unknown): string {
  const n = typeof ms === "number" ? ms : null;
  if (n === null || !Number.isFinite(n)) return "—";
  if (n < 0 || n > 5 * 60 * 1000) return "ساعت گوشی";
  return `${faNum(Math.round(n / 1000))} ثانیه`;
}

export function CodesLog() {
  const [filter, setFilter] = useState<LogFilter>("all");
  const log = useQuery({
    queryKey: ["forwarder", "codes-log"],
    queryFn: () => api<{ events: SmsEvent[]; count: number }>(`/sms/events?limit=${LIMIT}&stage=inbound`),
    refetchInterval: 15_000,
  });
  const events = log.data?.events ?? [];
  const filtered = filter === "all" ? events : events.filter((e) => bucketOf(e.details.reason) === filter);

  return (
    <Section
      title="کدهای رسیده از گوشی"
      hint="آخرین ۱۰۰ پیامک رسیده از گوشی‌ها، و اینکه هرکدام کجا رفت"
      action={<ListFilter className="size-4 text-muted-foreground" aria-hidden />}
    >
      {!log.isLoading && !log.isError && (
        <CodesChart events={events} asOf={log.dataUpdatedAt} full={events.length >= LIMIT} />
      )}

      <div className="mb-3 flex flex-wrap gap-1.5" role="group" aria-label="فیلتر کدهای رسیده">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            aria-pressed={filter === f.key}
            onClick={() => setFilter(f.key)}
            className={cn(
              "rounded-full border px-3 py-1 text-xs font-medium outline-none transition hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring",
              filter === f.key ? "border-primary/50 bg-primary/10 text-foreground" : "text-muted-foreground",
            )}
          >
            {f.label}
          </button>
        ))}
      </div>

      {log.isLoading ? (
        <ListSkeleton rows={4} />
      ) : log.isError ? (
        <ErrorNote error={log.error} illustration={<IsoAlert />} />
      ) : !filtered.length ? (
        <Empty illustration={<Lottie animationData={emptyLottie} className="max-w-[100px]" />}>
          {events.length ? "کدی با این فیلتر نیست." : "هنوز کدی از گوشی‌ها نرسیده است."}
        </Empty>
      ) : (
        <ul tabIndex={0} aria-label="فهرست کدهای رسیده" className="grid max-h-[32rem] gap-1.5 overflow-y-auto pe-1">
          {filtered.map((e, i) => {
            const b = bucketOf(e.details.reason);
            return (
              <motion.li
                key={e.id}
                // a row mounts once: the 15 s refetch keeps every id, so only a code
                // that has just arrived (or a filter that brings rows back) plays this
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4, delay: Math.min(i, 10) * 0.03, ease: [0.22, 1, 0.36, 1] }}
                className="flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-xs"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    {e.details.account && <span dir="ltr" className="font-mono">{e.details.account}</span>}
                    <ToneBadge tone={BUCKET_TONE[b]}>{e.details.reason ? (FW_REASON[e.details.reason] ?? e.details.reason) : e.message}</ToneBadge>
                  </div>
                  <div className="mt-0.5 text-muted-foreground">{e.at ? faDate(new Date(e.at), { dateStyle: "short", timeStyle: "medium" }) : "—"}</div>
                </div>
                <div className="shrink-0 text-muted-foreground tabular">{latencyLabel(e.details.latency_ms)}</div>
              </motion.li>
            );
          })}
        </ul>
      )}
    </Section>
  );
}
