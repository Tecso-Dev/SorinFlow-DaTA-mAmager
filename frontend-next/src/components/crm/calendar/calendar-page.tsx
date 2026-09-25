"use client";

// تقویم — Jalali month/week/day calendar over the three sources
// GET /crm/calendar mixes: real appointments (editable) plus Task.due_date
// and Reminder.remind_at, overlaid read-only. See app/api/routes/crm.py
// _calendar_rows and app/models/crm_models.py CalendarEvent.EVENT_TYPES.

import { CalendarDays, ChevronLeft, ChevronRight, FileSpreadsheet, Plus } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { PageHeader, Section, Toolbar } from "@/components/panel/kit";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "cn";
import { Reveal } from "@/components/viz";
import { toast } from "@/components/toaster";
import { api } from "@/lib/api";
import { exportHref, qs } from "@/lib/crm";
import { faNum } from "@/lib/format";
import { MONTHS, addJMonths, fromJalali, jParts, monthLength, startOfJMonth, weekdaySat0 } from "@/lib/jalali";
import { EventDialog } from "./event-dialog";
import { MonthView } from "./month-view";
import { TimeGrid } from "./time-grid";
import { UpcomingStrip } from "./upcoming";
import { EVENT_TYPES, localIso, type CalendarRow } from "./types";

type View = "month" | "week" | "day";

function jStartOfWeek(d: Date): Date {
  const x = new Date(d);
  x.setHours(12, 0, 0, 0);
  x.setDate(x.getDate() - weekdaySat0(x));
  x.setHours(0, 0, 0, 0);
  return x;
}

function range(view: View, cursor: Date): { start: Date; end: Date; title: string } {
  if (view === "day") {
    const s = new Date(cursor);
    s.setHours(0, 0, 0, 0);
    const e = new Date(s);
    e.setDate(e.getDate() + 1);
    const { y, m, d } = jParts(s);
    return { start: s, end: e, title: `${faNum(d)} ${MONTHS[m - 1]} ${faNum(y, { useGrouping: false })}` };
  }
  if (view === "week") {
    const s = jStartOfWeek(cursor);
    const e = new Date(s);
    e.setDate(e.getDate() + 7);
    const last = new Date(s);
    last.setDate(last.getDate() + 6);
    const a = jParts(s), b = jParts(last);
    const title = a.y === b.y && a.m === b.m
      ? `${faNum(a.d)} تا ${faNum(b.d)} ${MONTHS[a.m - 1]} ${faNum(a.y, { useGrouping: false })}`
      : `${faNum(a.d)} ${MONTHS[a.m - 1]} تا ${faNum(b.d)} ${MONTHS[b.m - 1]} ${faNum(b.y, { useGrouping: false })}`;
    return { start: s, end: e, title };
  }
  const first = startOfJMonth(cursor);
  const s = jStartOfWeek(first);
  const e = new Date(s);
  e.setDate(e.getDate() + 42);
  const { y, m } = jParts(first);
  return { start: s, end: e, title: `${MONTHS[m - 1]} ${faNum(y, { useGrouping: false })}` };
}

export function CalendarPage() {
  const [view, setView] = useState<View>("month");
  const [cursor, setCursor] = useState(() => new Date());
  const [typeFilter, setTypeFilter] = useState("");
  const [dialog, setDialog] = useState<{ open: boolean; seq: number; eventId?: number | null; draft?: Partial<CalendarRow> }>({ open: false, seq: 0 });
  const qc = useQueryClient();

  const { start, end, title } = useMemo(() => range(view, cursor), [view, cursor]);

  const q = useQuery({
    queryKey: ["crm", "calendar", "grid", +start, +end, typeFilter],
    queryFn: () =>
      api<{ items: CalendarRow[]; total: number }>(
        `/crm/calendar${qs({ date_from: localIso(start), date_to: localIso(end), event_type: typeFilter })}`,
      ),
  });

  function invalidate() {
    qc.invalidateQueries({ queryKey: ["crm", "calendar"] });
  }

  function step(dir: 1 | -1) {
    if (view === "month") {
      // addJMonths lands on the 1st; keep the day of month (clamped) so
      // switching to week/day right after stays on the expected date
      const { d } = jParts(cursor);
      const target = addJMonths(cursor, dir);
      const { y, m } = jParts(target);
      setCursor(fromJalali(y, m, Math.min(d, monthLength(y, m))));
    } else {
      const x = new Date(cursor);
      x.setDate(x.getDate() + dir * (view === "week" ? 7 : 1));
      setCursor(x);
    }
  }

  function openRow(row: CalendarRow) {
    if (row.kind !== "event") {
      toast.info(
        row.kind === "task" ? "این یک وظیفه است" : "این یک یادآور است",
        row.kind === "task" ? "وظیفه‌ها در تب «وظایف» ویرایش می‌شوند" : "یادآورها در تب «یادآورها» ویرایش می‌شوند",
      );
      return;
    }
    setDialog((d) => ({ open: true, seq: d.seq + 1, eventId: row.id }));
  }

  function createOnDay(d: Date) {
    const at = new Date(d);
    if (at.getHours() === 0 && at.getMinutes() === 0) at.setHours(10, 0, 0, 0);
    setDialog((s) => ({ open: true, seq: s.seq + 1, eventId: null, draft: { start_at: localIso(at) } }));
  }

  const rows = q.data?.items ?? [];

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        icon={CalendarDays}
        title="تقویم"
        hint="بازدیدها، نشست‌ها و تماس‌های زمان‌بندی‌شده، کنار وظایف و یادآورهای سررسیددار"
        actions={
          <Button onClick={() => createOnDay(new Date())} className="gap-1.5">
            <Plus className="size-4" /> قرار جدید
          </Button>
        }
      />

      <Reveal>
        <Section title="قرارهای پیشِ رو" hint="۷ روز آینده">
          <UpcomingStrip onOpenRow={openRow} />
        </Section>
      </Reveal>

      <Reveal delay={0.05}>
        <Section
          bodyClassName="p-3 sm:p-4"
          action={
            <a
              href={exportHref("/crm/calendar/export/excel", { date_from: localIso(start), date_to: localIso(end), event_type: typeFilter })}
              download
              className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-input px-2.5 text-sm font-medium hover:bg-accent"
            >
              <FileSpreadsheet className="size-4" /> خروجی اکسل
            </a>
          }
        >
          <Toolbar className="mb-3 justify-between">
            <div className="flex items-center gap-1.5">
              <Button variant="ghost" size="icon" className="size-8" aria-label="قبلی" onClick={() => step(-1)}>
                <ChevronRight className="size-4" />
              </Button>
              <h2 className="w-40 text-center text-sm font-bold sm:w-52 sm:text-base">{title}</h2>
              <Button variant="ghost" size="icon" className="size-8" aria-label="بعدی" onClick={() => step(1)}>
                <ChevronLeft className="size-4" />
              </Button>
              <Button variant="outline" size="sm" onClick={() => setCursor(new Date())}>امروز</Button>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <select
                aria-label="فیلتر نوع رویداد"
                value={typeFilter}
                onChange={(e) => setTypeFilter(e.target.value)}
                className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring [&>option]:bg-popover"
              >
                <option value="">همهٔ انواع</option>
                {Object.entries(EVENT_TYPES).map(([k, v]) => (
                  <option key={k} value={k}>{v.label}</option>
                ))}
              </select>
              {/* a switch of the same grid, not tabs of separate panels */}
              <div role="group" aria-label="نمای تقویم" className="inline-flex h-8 items-center rounded-lg bg-muted p-[3px]">
                {([["month", "ماه"], ["week", "هفته"], ["day", "روز"]] as const).map(([v, label]) => (
                  <button
                    key={v}
                    type="button"
                    aria-pressed={view === v}
                    onClick={() => setView(v)}
                    className={cn(
                      "h-full rounded-md px-2.5 text-xs font-medium text-muted-foreground transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      view === v && "bg-background text-foreground shadow-sm dark:bg-input/30",
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </Toolbar>

          {q.isLoading ? (
            <Skeleton className="h-[480px] w-full rounded-xl" />
          ) : q.isError ? (
            <div className="grid h-40 place-items-center text-sm text-muted-foreground">بارگیری تقویم ناموفق بود</div>
          ) : view === "month" ? (
            <MonthView gridStart={start} rows={rows} onOpenRow={openRow} onCreateDay={createOnDay} />
          ) : (
            <TimeGrid gridStart={start} days={view === "week" ? 7 : 1} rows={rows} onOpenRow={openRow} onCreateDay={createOnDay} />
          )}
        </Section>
      </Reveal>

      <EventDialog
        key={dialog.seq}
        open={dialog.open}
        onOpenChange={(o) => setDialog((d) => ({ ...d, open: o }))}
        eventId={dialog.eventId}
        draft={dialog.draft}
        onSaved={invalidate}
        onDeleted={invalidate}
      />
    </div>
  );
}
