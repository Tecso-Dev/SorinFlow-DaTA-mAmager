"use client";

// «کدهای رسیده از گوشی»: GET /sms/events?stage=inbound, gated by the `sms`
// permission at the router level (app/api/routes/__init__.py) — separate
// from `forwarder`, so a user can have one without the other. The parent
// hides this whole card rather than disabling it when that permission is
// missing; this component assumes it is allowed to be here.

import { ListFilter } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { cn } from "cn";
import { Empty, ErrorNote, ListSkeleton, Section, ToneBadge } from "@/components/panel/kit";
import { api } from "@/lib/api";
import { faDate, faNum } from "@/lib/format";
import type { Tone } from "@/lib/crm";
import { bucketOf, FW_REASON, type LogFilter } from "./shared";
import type { SmsEvent } from "./types";

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
    queryFn: () => api<{ events: SmsEvent[]; count: number }>("/sms/events?limit=100&stage=inbound"),
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
        <ErrorNote error={log.error} />
      ) : !filtered.length ? (
        <Empty>کدی با این فیلتر نیست.</Empty>
      ) : (
        <ul className="grid gap-1.5">
          {filtered.map((e) => {
            const b = bucketOf(e.details.reason);
            return (
              <li key={e.id} className="flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-xs">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    {e.details.account && <span dir="ltr" className="font-mono">{e.details.account}</span>}
                    <ToneBadge tone={BUCKET_TONE[b]}>{e.details.reason ? (FW_REASON[e.details.reason] ?? e.details.reason) : e.message}</ToneBadge>
                  </div>
                  <div className="mt-0.5 text-muted-foreground">{e.at ? faDate(new Date(e.at), { dateStyle: "short", timeStyle: "medium" }) : "—"}</div>
                </div>
                <div className="shrink-0 text-muted-foreground tabular">{latencyLabel(e.details.latency_ms)}</div>
              </li>
            );
          })}
        </ul>
      )}
    </Section>
  );
}
