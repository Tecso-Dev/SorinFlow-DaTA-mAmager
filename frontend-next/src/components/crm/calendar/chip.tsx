"use client";

// One row on the grid, whatever its kind. An event is a real appointment
// (editable); a task or reminder is only overlaid here from another table —
// CalendarEvent.to_dict()/_task_as_event/_reminder_as_event all carry
// "kind", which is what tells the two apart, never the event_type alone.

import { Bell, CheckSquare2, MessageCircle } from "lucide-react";
import { cn } from "cn";
import { faDate } from "@/lib/format";
import type { CalendarRow } from "./types";

const KIND_ICON = { task: CheckSquare2, reminder: Bell } as const;

export function EventChip({
  row, onClick, dense = false,
}: { row: CalendarRow; onClick: () => void; dense?: boolean }) {
  const overlay = row.kind !== "event";
  const Icon = overlay ? KIND_ICON[row.kind as "task" | "reminder"] : null;
  const time = row.start_at && !row.all_day ? faDate(new Date(row.start_at), { hour: "2-digit", minute: "2-digit" }) : null;
  const done = row.status === "done";
  const canceled = row.status === "canceled";
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      title={`${row.title} — ${row.type_label}${overlay ? " (فقط قابل مشاهده)" : ""}`}
      style={{ "--c": row.color } as React.CSSProperties}
      className={cn(
        "flex w-full items-center gap-1 truncate rounded-md border-s-2 px-1.5 py-0.5 text-start text-[11px] leading-4 outline-none transition-colors",
        // the event's own colour lives on the accent bar and the icon only —
        // a neutral, already-vetted token background keeps the text itself
        // at a guaranteed contrast no matter what colour the backend sends
        "border-s-[var(--c)] bg-muted/60 text-foreground hover:bg-accent",
        "focus-visible:ring-2 focus-visible:ring-ring",
        // status/kind is shown by shape (dashed border, strikethrough) and
        // an explicit muted colour, not element opacity — opacity fades the
        // text toward the day cell's own background and can fall under the
        // WCAG contrast ratio depending on the event's own colour and theme
        overlay && "border-dashed",
        (done || canceled) && "line-through text-muted-foreground",
        dense && "px-1 py-px text-[10px]",
      )}
    >
      {Icon && <Icon className="size-3 shrink-0" style={{ color: row.color }} />}
      {row.sms_reminder && <MessageCircle className={cn("size-3 shrink-0", row.sms_sent ? "opacity-60" : "text-primary")} />}
      {time && <b className="shrink-0 tabular">{time}</b>}
      <span className="truncate">{row.title}</span>
    </button>
  );
}

/** A small colour dot — the compact-cell affordance on a phone month view. */
export function EventDot({ row }: { row: CalendarRow }) {
  return <span className="size-1.5 shrink-0 rounded-full" style={{ background: row.color }} title={row.title} />;
}
