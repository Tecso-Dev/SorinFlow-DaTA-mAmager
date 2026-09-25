"use client";

// A plain number field, western digits, dir="ltr" — for every price/area/
// room/rotation box in the scraper form. Kept as raw text (not <input
// type="number">) so a pasted Persian digit or a thousands separator from
// Divar's own page is accepted and cleaned rather than rejected outright.

import { Input } from "@/components/ui/input";
import { parseDigits } from "@/lib/format";

export function DigitsInput({
  value, onChange, placeholder, "aria-label": ariaLabel, id, className,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  "aria-label"?: string;
  id?: string;
  className?: string;
}) {
  return (
    <Input
      id={id}
      dir="ltr"
      inputMode="numeric"
      aria-label={ariaLabel}
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(parseDigits(e.target.value).replace(/[^\d]/g, ""))}
      className={className ?? "tabular"}
    />
  );
}
