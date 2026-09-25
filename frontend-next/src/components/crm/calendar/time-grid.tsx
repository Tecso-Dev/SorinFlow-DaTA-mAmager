"use client";

// Week/day view: an hourly grid with each day a column. All-day rows sit in
// a strip above it; timed rows are positioned by clock time and packed into
// side-by-side lanes when two overlap, the way every calendar app does it.

import { cn } from "cn";
import { faDate, faNum } from "@/lib/format";
import { WEEKDAYS_SHORT, jParts, weekdaySat0 } from "@/lib/jalali";
import { EventChip } from "./chip";
import { sameDay, type CalendarRow } from "./types";

const HOUR_START = 7;
const HOUR_END = 22;         // exclusive
const HOUR_PX = 52;
const DEFAULT_MIN = 45;      // a block's height when no end_at is set

type Positioned = { row: CalendarRow; top: number; height: number; lane: number; lanes: number };

/** Greedy interval packing: events that overlap in time share the column
 *  side by side; ones that do not reuse the same lane. */
function packDay(rows: CalendarRow[]): Positioned[] {
  const timed = rows
    .filter((r) => r.start_at && !r.all_day)
    .map((r) => {
      const start = new Date(r.start_at as string);
      const end = r.end_at ? new Date(r.end_at) : new Date(start.getTime() + DEFAULT_MIN * 60_000);
      const sMin = start.getHours() * 60 + start.getMinutes();
      const eMin = Math.max(sMin + 15, end.getHours() * 60 + end.getMinutes());
      return { row: r, sMin, eMin };
    })
    .sort((a, b) => a.sMin - b.sMin);

  const laneEnds: number[] = [];
  const placed = timed.map((t) => {
    let lane = laneEnds.findIndex((end) => end <= t.sMin);
    if (lane === -1) {
      lane = laneEnds.length;
      laneEnds.push(t.eMin);
    } else {
      laneEnds[lane] = t.eMin;
    }
    return { ...t, lane };
  });
  // cluster overlapping runs to size their lane count together
  return placed.map((p, i) => {
    const overlapping = placed.filter((q) => q.sMin < p.eMin && q.eMin > p.sMin);
    const lanes = Math.max(1, ...overlapping.map((q) => q.lane + 1));
    const top = ((p.sMin - HOUR_START * 60) / 60) * HOUR_PX;
    const height = Math.max(20, ((p.eMin - p.sMin) / 60) * HOUR_PX - 2);
    void i;
    return { row: p.row, top, height, lane: p.lane, lanes };
  });
}

export function TimeGrid({
  gridStart, days, rows, onOpenRow, onCreateDay,
}: { gridStart: Date; days: number; rows: CalendarRow[]; onOpenRow: (r: CalendarRow) => void; onCreateDay: (d: Date) => void }) {
  const cols = Array.from({ length: days }, (_, i) => {
    const d = new Date(gridStart);
    d.setDate(d.getDate() + i);
    d.setHours(0, 0, 0, 0);
    return d;
  });
  const hours = Array.from({ length: HOUR_END - HOUR_START }, (_, i) => HOUR_START + i);
  const today = new Date();
  const rowsOn = (d: Date) => rows.filter((r) => r.start_at && sameDay(new Date(r.start_at), d));

  return (
    <div className="overflow-x-auto rounded-xl border">
      <div className="min-w-[560px]">
        {/* header */}
        <div className="grid border-b bg-muted/40" style={{ gridTemplateColumns: `3.25rem repeat(${days}, 1fr)` }}>
          <div />
          {cols.map((d) => (
            <div key={+d} className="border-s px-1.5 py-2 text-center">
              <div className="text-[11px] text-muted-foreground">{WEEKDAYS_SHORT[weekdaySat0(d)]}</div>
              <div className={cn("mx-auto mt-0.5 grid size-6 place-items-center rounded-full text-xs font-bold tabular", sameDay(d, today) && "bg-primary text-primary-foreground")}>
                {faNum(jParts(d).d)}
              </div>
            </div>
          ))}
        </div>

        {/* all-day strip */}
        <div className="grid border-b" style={{ gridTemplateColumns: `3.25rem repeat(${days}, 1fr)` }}>
          <div className="px-1.5 py-1 text-end text-[10px] text-muted-foreground">تمام‌روز</div>
          {cols.map((d) => (
            <div key={+d} className="flex min-h-[28px] flex-col gap-0.5 border-s p-1">
              {rowsOn(d).filter((r) => r.all_day).map((r) => (
                <EventChip key={`${r.kind}-${r.id}`} row={r} onClick={() => onOpenRow(r)} dense />
              ))}
            </div>
          ))}
        </div>

        {/* hourly grid */}
        <div className="relative grid" style={{ gridTemplateColumns: `3.25rem repeat(${days}, 1fr)` }}>
          <div>
            {hours.map((h) => (
              <div key={h} className="relative border-t text-end text-[10px] text-muted-foreground" style={{ height: HOUR_PX }}>
                <span className="absolute -top-2 end-1.5 bg-card px-0.5">{faNum(h)}</span>
              </div>
            ))}
          </div>
          {cols.map((d) => {
            const packed = packDay(rowsOn(d));
            return (
              <div
                key={+d}
                className="relative cursor-pointer border-s"
                style={{ height: HOUR_PX * hours.length }}
                onClick={(e) => {
                  if (e.target !== e.currentTarget) return;
                  const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
                  const mins = ((e.clientY - rect.top) / HOUR_PX) * 60 + HOUR_START * 60;
                  const at = new Date(d);
                  at.setMinutes(Math.round(mins / 15) * 15);
                  onCreateDay(at);
                }}
              >
                {hours.map((h) => <div key={h} className="border-t" style={{ height: HOUR_PX }} />)}
                {packed.map(({ row, top, height, lane, lanes }) => (
                  <div
                    key={`${row.kind}-${row.id}`}
                    className="absolute p-px"
                    style={{ top, height, insetInlineStart: `${(lane / lanes) * 100}%`, width: `${100 / lanes}%` }}
                  >
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        onOpenRow(row);
                      }}
                      title={`${row.title} — ${faDate(new Date(row.start_at as string), { hour: "2-digit", minute: "2-digit" })}`}
                      style={{ "--c": row.color } as React.CSSProperties}
                      className={cn(
                        // the event's own colour lives on the accent bar
                        // only, same reasoning as chip.tsx's EventChip
                        "h-full w-full overflow-hidden rounded-md border-s-2 border-s-[var(--c)] bg-muted/60 text-foreground px-1.5 py-0.5 text-start text-[10px] leading-3.5 outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring",
                        row.kind !== "event" && "border-dashed",
                        (row.status === "canceled" || row.status === "done") && "text-muted-foreground line-through",
                      )}
                    >
                      <b className="tabular">{faDate(new Date(row.start_at as string), { hour: "2-digit", minute: "2-digit" })}</b>{" "}
                      <span className="truncate">{row.title}</span>
                    </button>
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
