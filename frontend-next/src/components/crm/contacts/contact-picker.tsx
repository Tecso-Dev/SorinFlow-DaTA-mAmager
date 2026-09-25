"use client";

// A search-as-you-type contact picker (GET /crm/contacts?search=) for a
// deal's buyer / seller — the old panel had the office type contact IDs by
// hand. Exported for the deals tab too.

import { Check, ChevronsUpDown, X } from "lucide-react";
import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { api } from "@/lib/api";

export type PickedContact = { id: number; name: string; phone?: string | null };

export function ContactPicker({
  id, value, onChange, placeholder = "جستجوی مخاطب…",
}: { id?: string; value: PickedContact | null; onChange: (c: PickedContact | null) => void; placeholder?: string }) {
  const [open, setOpen] = useState(false);
  const [term, setTerm] = useState("");
  const [debounced, setDebounced] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const q = useQuery({
    queryKey: ["crm", "contacts-picker", debounced],
    queryFn: () => api<{ items: PickedContact[] }>(`/crm/contacts?limit=8&search=${encodeURIComponent(debounced)}`),
    enabled: open,
  });

  function handleTerm(v: string) {
    setTerm(v);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setDebounced(v.trim()), 300);
  }

  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (!o) { setTerm(""); setDebounced(""); } }}>
      <PopoverTrigger asChild>
        <Button id={id} type="button" variant="outline" role="combobox" aria-expanded={open} className="h-9 w-full justify-between font-normal">
          <span className={cn("truncate", !value && "text-muted-foreground")}>{value ? value.name : placeholder}</span>
          <ChevronsUpDown className="size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 max-w-[90vw] p-0" align="start">
        <div className="p-2">
          <Input autoFocus dir="rtl" value={term} onChange={(e) => handleTerm(e.target.value)} placeholder="نام یا شماره…" />
        </div>
        <div className="max-h-56 overflow-y-auto border-t p-1">
          {value && (
            <button
              type="button"
              onClick={() => { onChange(null); setOpen(false); }}
              className="flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-sm text-destructive hover:bg-destructive/10"
            >
              <X className="size-3.5" /> پاک کردن انتخاب
            </button>
          )}
          {open && q.isLoading && <p className="px-2 py-3 text-center text-xs text-muted-foreground">در حال جستجو…</p>}
          {open && q.data && q.data.items.length === 0 && <p className="px-2 py-3 text-center text-xs text-muted-foreground">چیزی پیدا نشد</p>}
          {q.data?.items.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => { onChange(c); setOpen(false); }}
              className="flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-start text-sm hover:bg-accent"
            >
              <span className="truncate">{c.name}</span>
              <span className="flex shrink-0 items-center gap-1.5">
                {c.phone && <span className="text-xs text-muted-foreground tabular" dir="ltr">{c.phone}</span>}
                {value?.id === c.id && <Check className="size-3.5 text-primary" />}
              </span>
            </button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
