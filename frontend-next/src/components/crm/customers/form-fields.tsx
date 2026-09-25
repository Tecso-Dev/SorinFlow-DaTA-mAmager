"use client";

// Small form controls shared by the customer and contact dialogs: a money
// field that groups digits as it is typed, and a city field suggested from
// the office's own scraped cities (GET /scraper/cities — any staff account,
// not gated on the "scraper" permission, since every CRM form uses it).

import { useId } from "react";
import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { parseDigits } from "@/lib/format";

/** Digits only, grouped with commas as they are typed — the office keys
 *  money in toman by hand, and ungrouped ten-digit numbers are easy to
 *  mistype. `price()` (lib/crm.ts) shows the same value once saved. */
export function MoneyInput({
  id, value, onChange, placeholder = "۰", className,
}: { id?: string; value: number | null; onChange: (n: number | null) => void; placeholder?: string; className?: string }) {
  const text = value !== null && value !== undefined ? value.toLocaleString("en-US") : "";
  function handle(raw: string) {
    const digits = parseDigits(raw).replace(/[^\d]/g, "");
    onChange(digits ? Number(digits) : null);
  }
  return (
    <Input
      id={id}
      dir="ltr"
      inputMode="numeric"
      placeholder={placeholder}
      value={text}
      onChange={(e) => handle(e.target.value)}
      className={cn("text-end tabular", className)}
    />
  );
}

type City = { slug: string; name: string; province: string };

/** A text input with the office's scraped cities as suggestions (a native
 *  datalist — reliable on a phone, and free text still works for a city not
 *  in the list). */
export function CityField({
  id, value, onChange, placeholder = "شهر", className,
}: { id?: string; value: string; onChange: (v: string) => void; placeholder?: string; className?: string }) {
  const listId = useId();
  const { data } = useQuery({
    queryKey: ["scraper", "cities"],
    queryFn: () => api<City[]>("/scraper/cities"),
    staleTime: 60 * 60_000,
    retry: false,
  });
  return (
    <>
      <Input id={id} list={listId} value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} className={className} />
      <datalist id={listId}>
        {(data ?? []).map((c) => (
          <option key={c.slug} value={c.name} />
        ))}
      </datalist>
    </>
  );
}
