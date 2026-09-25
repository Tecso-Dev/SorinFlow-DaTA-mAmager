"use client";

// The month grid: Saturday first, today ringed, up to three chips a cell
// (a fourth becomes «+N») on a desktop, dots plus a below-grid day list on a
// phone — a dense table of text never fits 390px wide.

import { Plus } from "lucide-react";
import { motion } from "motion/react";
import { useState } from "react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Empty } from "@/components/panel/kit";
import { faDate, faNum } from "@/lib/format";
import { WEEKDAYS, WEEKDAYS_SHORT, jParts } from "@/lib/jalali";
import { EventChip, EventDot } from "./chip";
import { sameDay, type CalendarRow } from "./types";

const DESKTOP_CAP = 3;

export function MonthView({
  gridStart, rows, onOpenRow, onCreateDay,
}: { gridStart: Date; rows: CalendarRow[]; onOpenRow: (r: CalendarRow) => void; onCreateDay: (d: Date) => void }) {
  const today = new Date();
  const [selected, setSelected] = useState<Date>(() => {
    const t = new Date();
    t.setHours(0, 0, 0, 0);
    return t;
  });
  const monthOf = jParts(new Date(gridStart.getFullYear(), gridStart.getMonth(), gridStart.getDate() + 10)).m;

  const days = Array.from({ length: 42 }, (_, i) => {
    const d = new Date(gridStart);
    d.setDate(d.getDate() + i);
    d.setHours(0, 0, 0, 0);
    return d;
  });
  const rowsOn = (d: Date) =>
    rows.filter((r) => r.start_at && sameDay(new Date(r.start_at), d))
      .sort((a, b) => (a.start_at ?? "").localeCompare(b.start_at ?? ""));

  function cellClick(d: Date) {
    if (typeof window !== "undefined" && window.innerWidth >= 640) {
      onCreateDay(d);
    } else {
      setSelected(d);
    }
  }

  const selectedRows = rowsOn(selected);

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-7 overflow-hidden rounded-xl border">
        {WEEKDAYS.map((w, i) => (
          <div key={w} className="border-b bg-muted/40 px-1.5 py-1.5 text-center text-[11px] font-semibold text-muted-foreground">
            <span className="hidden sm:inline">{w}</span>
            <span className="sm:hidden">{WEEKDAYS_SHORT[i]}</span>
          </div>
        ))}
        {days.map((d, i) => {
          const rowsHere = rowsOn(d);
          const inMonth = jParts(d).m === monthOf;
          const isToday = sameDay(d, today);
          const isSelected = sameDay(d, selected);
          return (
            <motion.div
              key={i}
              // a plain, non-interactive cell: its own click is a mouse-only
              // convenience, so the day button and the chip buttons inside it
              // are never a control nested inside another one for a11y tools
              onClick={() => cellClick(d)}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.25, delay: Math.min(i * 0.006, 0.2) }}
              className={cn(
                "group relative flex min-h-[64px] cursor-pointer flex-col gap-0.5 border-b border-e p-1 text-start transition-colors sm:min-h-[104px] sm:p-1.5",
                "[&:nth-child(7n)]:border-e-0 hover:bg-accent/40",
                // the tint alone says "not this month" — an extra text
                // opacity on top of it can drop below the contrast floor
                !inMonth && "bg-muted/20 text-muted-foreground",
                isSelected && "bg-primary/[0.06] sm:bg-transparent",
              )}
            >
              <div className="flex items-center justify-between">
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    cellClick(d);
                  }}
                  aria-label={`روز ${faNum(jParts(d).d)}`}
                  className={cn(
                    "grid size-6 place-items-center rounded-full text-xs font-semibold tabular outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    isToday && "bg-primary text-primary-foreground",
                  )}
                >
                  {faNum(jParts(d).d)}
                </button>
                <Plus aria-hidden className="hidden size-3.5 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 sm:block" />
              </div>

              {/* desktop: up to three chips, «+N» beyond that */}
              <div className="hidden flex-col gap-0.5 sm:flex">
                {rowsHere.slice(0, DESKTOP_CAP).map((r) => (
                  <EventChip key={`${r.kind}-${r.id}`} row={r} onClick={() => onOpenRow(r)} dense />
                ))}
                {rowsHere.length > DESKTOP_CAP && (
                  <span className="px-1 text-[10px] text-muted-foreground">+{faNum(rowsHere.length - DESKTOP_CAP)} بیشتر</span>
                )}
              </div>

              {/* phone: coloured dots only — the list of the selected day sits below the grid */}
              <div className="flex flex-wrap gap-0.5 sm:hidden">
                {rowsHere.slice(0, 6).map((r) => (
                  <EventDot key={`${r.kind}-${r.id}`} row={r} />
                ))}
              </div>
            </motion.div>
          );
        })}
      </div>

      {/* phone-only: the selected day's events, spelled out */}
      <div className="rounded-xl border p-3 sm:hidden">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-bold">
            {sameDay(selected, today) ? "امروز" : faDate(selected, { weekday: "long", day: "numeric", month: "long" })}
          </h3>
          <Button size="sm" variant="outline" className="gap-1" onClick={() => onCreateDay(selected)}>
            <Plus className="size-3.5" /> قرار جدید
          </Button>
        </div>
        {selectedRows.length ? (
          <div className="flex flex-col gap-1.5">
            {selectedRows.map((r) => (
              <EventChip key={`${r.kind}-${r.id}`} row={r} onClick={() => onOpenRow(r)} />
            ))}
          </div>
        ) : (
          <Empty>قراری در این روز ثبت نشده است</Empty>
        )}
      </div>
    </div>
  );
}
