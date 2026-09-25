"use client";

// The city filter: a searchable dropdown (the old panel's initCityPicker),
// not a plain <select> — the list runs past a hundred Iranian cities and a
// typed prefix finds one faster than scrolling. Filters GET /scraper/cities
// client-side, since the whole list is one small request.

import { Check, ChevronsUpDown, MapPin } from "lucide-react";
import { useState } from "react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { City } from "./types";

export function CityPicker({
  id, cities, value, onChange, className,
}: { id?: string; cities: City[]; value: string; onChange: (name: string) => void; className?: string }) {
  const [open, setOpen] = useState(false);
  const [term, setTerm] = useState("");
  const filtered = term.trim()
    ? cities.filter((c) => c.name.includes(term.trim()) || c.province.includes(term.trim()))
    : cities;
  const label = value ? (cities.find((c) => c.name === value)?.name ?? value) : "همهٔ شهرها";

  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (!o) setTerm(""); }}>
      <PopoverTrigger asChild>
        <Button id={id} type="button" variant="outline" role="combobox" aria-expanded={open} className={cn("h-9 min-w-36 justify-between font-normal", className)}>
          <span className="flex min-w-0 items-center gap-1.5">
            <MapPin className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
            <span className={cn("truncate", !value && "text-muted-foreground")}>{label}</span>
          </span>
          <ChevronsUpDown className="size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-64 max-w-[90vw] p-0" align="start">
        <div className="p-2">
          <Input autoFocus dir="rtl" value={term} onChange={(e) => setTerm(e.target.value)} placeholder="جستجو…" aria-label="جستجوی شهر" />
        </div>
        <div className="max-h-64 overflow-y-auto border-t p-1">
          <button
            type="button"
            onClick={() => { onChange(""); setOpen(false); }}
            className="flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-start text-sm hover:bg-accent"
          >
            همهٔ شهرها
            {!value && <Check className="size-3.5 text-primary" />}
          </button>
          {filtered.length === 0 && <p className="px-2 py-3 text-center text-xs text-muted-foreground">چیزی پیدا نشد</p>}
          {filtered.map((c) => (
            <button
              key={c.slug}
              type="button"
              onClick={() => { onChange(c.name); setOpen(false); }}
              className="flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-start text-sm hover:bg-accent"
            >
              <span className="truncate">{c.name}</span>
              <span className="flex shrink-0 items-center gap-1.5">
                <span className="text-xs text-muted-foreground">{c.province}</span>
                {value === c.name && <Check className="size-3.5 text-primary" />}
              </span>
            </button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
