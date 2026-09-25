"use client";

// A Jalali date (and optionally time) field: type «1405/07/03» or pick from
// a month grid. The value is a Date (local wall clock) or null, so callers
// send date.toISOString() and never handle Jalali arithmetic themselves.

import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";
import { useState } from "react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { faNum } from "@/lib/format";
import {
  addJMonths, formatJalali, fromJalali, jParts, monthLength, MONTHS, parseJalali, startOfJMonth, weekdaySat0,
  WEEKDAYS_SHORT,
} from "@/lib/jalali";

export function JalaliDateInput({
  id, value, onChange, withTime = false, placeholder, className, "aria-label": ariaLabel, invalidText = "تاریخ درست نیست",
}: {
  id?: string;
  value: Date | null;
  onChange: (d: Date | null) => void;
  withTime?: boolean;
  placeholder?: string;
  className?: string;
  "aria-label"?: string;
  invalidText?: string;
}) {
  const [text, setText] = useState(value ? formatJalali(value, withTime) : "");
  const [bad, setBad] = useState(false);
  const [open, setOpen] = useState(false);
  const [shown, setShown] = useState<string | null>(value ? formatJalali(value, withTime) : null);

  // an outside change (a preset, a reset) replaces what is typed
  const external = value ? formatJalali(value, withTime) : null;
  if (external !== shown) {
    setShown(external);
    setText(external ?? "");
    setBad(false);
  }

  function commit(t: string) {
    setText(t);
    if (!t.trim()) {
      setBad(false);
      setShown(null);
      onChange(null);
      return;
    }
    const d = parseJalali(t);
    setBad(!d);
    if (d) {
      setShown(formatJalali(d, withTime));
      onChange(d);
    }
  }

  function pick(day: Date) {
    const d = new Date(day);
    if (withTime && value) d.setHours(value.getHours(), value.getMinutes());
    else if (withTime) d.setHours(10, 0);
    const t = formatJalali(d, withTime);
    setText(t);
    setShown(t);
    setBad(false);
    onChange(d);
    setOpen(false);
  }

  return (
    <div className={cn("grid gap-1", className)}>
      <div className="flex gap-1.5">
        <Input
          id={id}
          dir="ltr"
          inputMode="numeric"
          aria-label={ariaLabel}
          aria-invalid={bad || undefined}
          placeholder={placeholder ?? (withTime ? "1405/07/03 14:30" : "1405/07/03")}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={(e) => commit(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && commit((e.target as HTMLInputElement).value)}
          className="text-end tabular"
        />
        <Popover open={open} onOpenChange={setOpen}>
          <PopoverTrigger asChild>
            <Button type="button" variant="outline" size="icon" aria-label="انتخاب از تقویم" className="shrink-0">
              <CalendarDays className="size-4" />
            </Button>
          </PopoverTrigger>
          <PopoverContent align="end" className="w-72 p-3">
            <MonthGrid selected={value} onPick={pick} />
          </PopoverContent>
        </Popover>
      </div>
      {bad && <p className="text-xs text-destructive">{invalidText}</p>}
    </div>
  );
}

/** A Jalali month: Saturday first, today ringed, `selected` filled. */
export function MonthGrid({ selected, onPick }: { selected: Date | null; onPick: (d: Date) => void }) {
  const [cursor, setCursor] = useState(() => startOfJMonth(selected ?? new Date()));
  const { y, m } = jParts(cursor);
  const len = monthLength(y, m);
  const lead = weekdaySat0(cursor);
  const today = jParts(new Date());
  const sel = selected ? jParts(selected) : null;
  const cells: (number | null)[] = [...Array(lead).fill(null), ...Array.from({ length: len }, (_, i) => i + 1)];
  return (
    <div className="select-none">
      <div className="mb-2 flex items-center justify-between">
        <Button type="button" variant="ghost" size="icon" className="size-8" aria-label="ماه قبل" onClick={() => setCursor(addJMonths(cursor, -1))}>
          <ChevronRight className="size-4" />
        </Button>
        <div className="text-sm font-bold">
          {MONTHS[m - 1]} {faNum(y, { useGrouping: false })}
        </div>
        <Button type="button" variant="ghost" size="icon" className="size-8" aria-label="ماه بعد" onClick={() => setCursor(addJMonths(cursor, 1))}>
          <ChevronLeft className="size-4" />
        </Button>
      </div>
      <div className="grid grid-cols-7 gap-1 text-center text-[11px] text-muted-foreground">
        {WEEKDAYS_SHORT.map((w) => <span key={w}>{w}</span>)}
      </div>
      <div className="mt-1 grid grid-cols-7 gap-1">
        {cells.map((d, i) =>
          d === null ? (
            <span key={`e${i}`} />
          ) : (
            <button
              key={d}
              type="button"
              onClick={() => onPick(fromJalali(y, m, d))}
              className={cn(
                "grid aspect-square place-items-center rounded-md text-sm tabular hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring outline-none",
                today.y === y && today.m === m && today.d === d && "ring-1 ring-primary",
                sel && sel.y === y && sel.m === m && sel.d === d && "bg-primary text-primary-foreground hover:bg-primary",
              )}
            >
              {faNum(d)}
            </button>
          ),
        )}
      </div>
      <div className="mt-2 flex justify-center">
        <Button type="button" variant="ghost" size="sm" className="h-7 text-xs" onClick={() => onPick(new Date(new Date().setHours(0, 0, 0, 0)))}>
          امروز
        </Button>
      </div>
    </div>
  );
}
