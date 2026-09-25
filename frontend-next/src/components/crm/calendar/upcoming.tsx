"use client";

// «قرارهای پیشِ رو» — GET /crm/calendar/upcoming?days=7&limit=8, a horizontal
// strip above the grid so the week's next appointments never need scrolling
// through the month to find.

import { useQuery } from "@tanstack/react-query";
import { CalendarClock, MapPin } from "lucide-react";
import { cn } from "cn";
import { Empty, ErrorNote, ListSkeleton } from "@/components/panel/kit";
import { Tilt } from "@/components/viz";
import { api } from "@/lib/api";
import { faDate } from "@/lib/format";
import { sameDay, type CalendarRow } from "./types";

export function UpcomingStrip({ onOpenRow }: { onOpenRow: (r: CalendarRow) => void }) {
  const q = useQuery({
    queryKey: ["crm", "calendar", "upcoming"],
    queryFn: () => api<{ items: CalendarRow[]; total: number }>("/crm/calendar/upcoming?days=7&limit=8"),
    refetchInterval: 120_000,
  });

  if (q.isLoading) return <ListSkeleton rows={2} />;
  if (q.isError) return <ErrorNote error={q.error} />;
  const items = (q.data?.items ?? []).filter((r) => r.status !== "canceled");
  if (!items.length) return <Empty icon={CalendarClock}>قراری در ۷ روز آینده ثبت نشده است</Empty>;

  return (
    <div className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1 [scrollbar-width:thin]">
      {items.map((r) => {
        const d = new Date(r.start_at as string);
        const today = sameDay(d, new Date());
        return (
          <Tilt key={`${r.kind}-${r.id}`} max={4} className="shrink-0">
            <button
              type="button"
              onClick={() => onOpenRow(r)}
              style={{ "--c": r.color } as React.CSSProperties}
              className={cn(
                "flex w-52 flex-col gap-1 rounded-xl border-s-2 border-s-[var(--c)] bg-card p-2.5 text-start shadow-sm outline-none transition-colors hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-ring",
                r.kind !== "event" && "border-dashed",
              )}
            >
              <span className="flex items-center gap-1.5 text-[11px] font-semibold tabular">
                {/* the event colour lives on the accent bar and this dot —
                    an arbitrary backend colour used as small text often
                    fails contrast against the card background */}
                <span className="size-1.5 shrink-0 rounded-full" style={{ background: r.color }} aria-hidden />
                {today ? "امروز" : faDate(d, { day: "numeric", month: "short" })}
                {!r.all_day && <span className="text-muted-foreground">· {faDate(d, { hour: "2-digit", minute: "2-digit" })}</span>}
              </span>
              <span className="truncate text-sm font-medium">{r.title}</span>
              {r.location && (
                <span className="flex items-center gap-1 truncate text-[11px] text-muted-foreground">
                  <MapPin className="size-3 shrink-0" /> {r.location}
                </span>
              )}
            </button>
          </Tilt>
        );
      })}
    </div>
  );
}
