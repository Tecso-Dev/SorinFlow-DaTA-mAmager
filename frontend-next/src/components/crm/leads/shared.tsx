"use client";

// Small pieces the leads list, the lead drawer and the call queue share:
// badges, phone links, money, and the per-kind lead fields.

import { Copy, Files, Phone } from "lucide-react";
import { useState } from "react";
import { cn } from "cn";
import { ToneBadge } from "@/components/panel/kit";
import { Input } from "@/components/ui/input";
import { CORNER_OPTIONS, DIRECTION_OPTIONS, price } from "@/lib/crm";
import { faDate, faNum, parseDigits } from "@/lib/format";
import { can, useSession } from "@/lib/session";

/** root and super_admin: the old panel's «crm-superadmin-only». */
export function useIsSuper(): boolean {
  const user = useSession().data?.user;
  return can(user, { roles: ["root", "super_admin"] });
}

/** «۳ مهر ۱۴۰۵، ۱۴:۳۰» */
export function when(iso: string | null | undefined): string {
  if (!iso) return "";
  return faDate(new Date(iso), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

/** «۱۴۰۵/۷/۳» */
export function day(iso: string | null | undefined): string {
  if (!iso) return "";
  return faDate(new Date(iso), { year: "numeric", month: "numeric", day: "numeric" });
}

/** «۵ دقیقه پیش» */
export function ago(iso: string | null | undefined): string {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 0) return `${when(iso)}`;
  if (s < 60) return "همین حالا";
  if (s < 3600) return `${faNum(Math.floor(s / 60))} دقیقه پیش`;
  if (s < 86400) return `${faNum(Math.floor(s / 3600))} ساعت پیش`;
  return `${faNum(Math.floor(s / 86400))} روز پیش`;
}

/** Only digits and a leading + survive into a tel: link. */
export const telHref = (phone: string) => `tel:${phone.replace(/[^\d+]/g, "")}`;

export function PhoneLink({ phone, className, big }: { phone: string; className?: string; big?: boolean }) {
  return (
    <a
      href={telHref(phone)}
      dir="ltr"
      className={cn(
        "inline-flex items-center gap-1.5 rounded-lg font-bold tabular text-success outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring",
        big ? "text-base" : "text-sm",
        className,
      )}
    >
      <Phone className={big ? "size-4" : "size-3.5"} aria-hidden />
      {phone}
    </a>
  );
}

/** A tel link and a copy button, for a phone number shown in a drawer. */
export function PhoneWithCopy({ phone }: { phone: string }) {
  const [done, setDone] = useState(false);
  return (
    <span className="inline-flex items-center gap-1">
      <PhoneLink phone={phone} big />
      <button
        type="button"
        aria-label="کپی شماره"
        className="grid size-7 place-items-center rounded-md text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
        onClick={() => {
          navigator.clipboard?.writeText(phone).then(() => {
            setDone(true);
            setTimeout(() => setDone(false), 1200);
          }, () => undefined);
        }}
      >
        <Copy className={cn("size-3.5", done && "text-success")} />
      </button>
    </span>
  );
}

/** کد ملک, the same number the properties list shows. */
export function SerialBadge({ serial, className }: { serial: number | null | undefined; className?: string }) {
  if (serial === null || serial === undefined) return null;
  return (
    <span
      title="کد ملک"
      className={cn(
        "inline-flex items-center rounded-md border border-primary/25 bg-primary/8 px-1.5 py-px font-mono text-[11px] font-semibold tabular text-primary",
        className,
      )}
    >
      {faNum(serial, { useGrouping: false })}
    </span>
  );
}

/**
 * «املاکی»: the ad's own words say an agency posted it. Red when Divar
 * said «شخصی» (the two disagree), grey when the ad says so itself.
 */
export function AgencyBadge({ p }: { p: { agency_suspected?: boolean | null; agency_evidence?: string | null; advertiser_type?: string | null; lead_advertiser_type?: string | null } }) {
  if (!p.agency_suspected) return null;
  const declared = p.advertiser_type || p.lead_advertiser_type || "";
  const clash = declared === "personal";
  const ev = p.agency_evidence ? ` — «${p.agency_evidence}»` : "";
  const title = clash ? `متن آگهی می‌گوید مشاور املاک است${ev}؛ ولی دیوار آن را «شخصی» ثبت کرده` : `متن آگهی می‌گوید مشاور املاک است${ev}`;
  return (
    <span title={title}>
      <ToneBadge tone={clash ? "danger" : "neutral"}>املاکی</ToneBadge>
    </span>
  );
}

/** «احتمالاً تکراری» when the embeddings flagged the listing. */
export function DupBadge({ of }: { of: number | null | undefined }) {
  if (!of) return null;
  return (
    <span title="متن این آگهی تقریباً همان آگهی دیگری است؛ فقط یک نشانه است و چیزی ادغام یا حذف نشده.">
      <ToneBadge tone="violet">
        <Files className="size-3" aria-hidden />
        احتمالاً تکراری
      </ToneBadge>
    </span>
  );
}

/** A match score in the old panel's three tiers. */
export function scoreTone(score: number) {
  return score >= 75 ? "high" : score >= 50 ? "mid" : "low";
}

export function ScoreDial({ score, size = 48 }: { score: number; size?: number }) {
  const tier = scoreTone(score);
  const r = size / 2 - 4;
  const c = 2 * Math.PI * r;
  const color = tier === "high" ? "text-success" : tier === "mid" ? "text-warning" : "text-muted-foreground";
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }} title="میزان همخوانی">
      <svg viewBox={`0 0 ${size} ${size}`} className="-rotate-90" aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={r} className="fill-none stroke-muted" strokeWidth={4} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          className={cn("fill-none stroke-current transition-[stroke-dashoffset] duration-700", color)}
          strokeWidth={4}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - Math.min(100, Math.max(0, score)) / 100)}
        />
      </svg>
      <span className={cn("absolute inset-0 grid place-items-center text-xs font-black tabular", color)}>
        {faNum(score)}٪
      </span>
      <span className="sr-only">{tier === "high" ? "همخوانی زیاد" : tier === "mid" ? "همخوانی متوسط" : "همخوانی کم"}</span>
    </div>
  );
}

/** The money on a listing: a sale price, or a rent's two halves. */
export function listingMoney(p: { listing_type?: string | null; price?: number | null; total_price?: number | null; deposit?: number | null; rent_price?: number | null }): string {
  if (p.listing_type === "rent") {
    const parts = [p.deposit ? `رهن ${price(p.deposit)}` : "", p.rent_price ? `اجاره ${price(p.rent_price)}` : ""].filter(Boolean);
    return parts.join(" · ") || "—";
  }
  return price(p.total_price || p.price);
}

/** A whole number typed with any digits and separators → number, or null. */
export function toInt(s: string): number | null {
  const d = parseDigits(s).replace(/[^\d]/g, "");
  return d ? Number(d) : null;
}

/** A toman amount typed in latin or Persian digits, grouped as it is typed,
 *  with the old panel's «۳٫۵ میلیارد» under it. */
export function MoneyInput({
  id, value, onChange, placeholder, "aria-label": ariaLabel, className, hint = true,
}: {
  id?: string; value: number | null; onChange: (v: number | null) => void; placeholder?: string;
  "aria-label"?: string; className?: string; hint?: boolean;
}) {
  return (
    <div className={cn("grid gap-1", className)}>
      <Input
        id={id}
        dir="ltr"
        inputMode="numeric"
        aria-label={ariaLabel}
        placeholder={placeholder}
        value={value === null ? "" : value.toLocaleString("en-US")}
        onChange={(e) => onChange(toInt(e.target.value))}
        className="text-end tabular"
      />
      {hint && value ? <span className="text-[11px] text-muted-foreground">{price(value)}</span> : null}
    </div>
  );
}

/* ───────────────────────── the per-kind lead fields ───────────────────────── */

export type KindField = { key: string; label: string; type: "num" | "text" | "bool" | "pick"; options?: string[] };

/** app.js LEAD_KIND_FIELDS: what a hand-entered lead asks for, by kind. */
export const LEAD_KIND_FIELDS: Record<string, KindField[]> = {
  apartment: [
    { key: "area", label: "متراژ", type: "num" },
    { key: "floor", label: "طبقه", type: "num" },
    { key: "units_per_floor", label: "تعداد واحد در طبقه", type: "num" },
    { key: "rooms", label: "تعداد خواب", type: "num" },
    { key: "year_built", label: "سال ساخت", type: "num" },
    { key: "has_elevator", label: "آسانسور", type: "bool" },
    { key: "has_parking", label: "پارکینگ", type: "bool" },
    { key: "has_storage", label: "انباری", type: "bool" },
    { key: "cabinets", label: "کابینت", type: "text" },
    { key: "closet", label: "کمد دیواری", type: "bool" },
    { key: "flooring", label: "پوشش کف", type: "text" },
    { key: "has_balcony", label: "بالکن", type: "bool" },
    { key: "delivery_date", label: "تاریخ تحویل", type: "text" },
    { key: "hvac", label: "گرمایش و سرمایش", type: "text" },
    { key: "document_type", label: "سند", type: "text" },
    { key: "building_direction", label: "جهت", type: "pick", options: DIRECTION_OPTIONS },
    { key: "corner_type", label: "نبش", type: "pick", options: CORNER_OPTIONS },
  ],
  villa: [
    { key: "land_area", label: "متراژ زمین", type: "num" },
    { key: "built_area", label: "زیربنا", type: "num" },
    { key: "total_floors", label: "تعداد طبقات", type: "num" },
    { key: "rooms", label: "تعداد خواب", type: "num" },
    { key: "year_built", label: "سال ساخت", type: "num" },
    { key: "has_parking", label: "پارکینگ", type: "bool" },
    { key: "has_storage", label: "انباری", type: "bool" },
    { key: "has_balcony", label: "بالکن", type: "bool" },
    { key: "cabinets", label: "کابینت", type: "text" },
    { key: "closet", label: "کمد دیواری", type: "bool" },
    { key: "flooring", label: "پوشش کف", type: "text" },
    { key: "yard", label: "حیاط", type: "text" },
    { key: "document_type", label: "سند", type: "text" },
    { key: "position", label: "موقعیت", type: "text" },
    { key: "delivery_date", label: "تاریخ تحویل", type: "text" },
    { key: "hvac", label: "گرمایش و سرمایش", type: "text" },
    { key: "building_direction", label: "جهت", type: "pick", options: DIRECTION_OPTIONS },
    { key: "corner_type", label: "نبش", type: "pick", options: CORNER_OPTIONS },
  ],
  shop: [
    { key: "area", label: "متراژ", type: "num" },
    { key: "frontage", label: "دهنه (متر)", type: "num" },
    { key: "height", label: "ارتفاع (متر)", type: "text" },
    { key: "mezzanine", label: "نیم‌طبقه", type: "text" },
    { key: "document_type", label: "سند", type: "text" },
    { key: "building_direction", label: "جهت", type: "pick", options: DIRECTION_OPTIONS },
    { key: "corner_type", label: "نبش", type: "pick", options: CORNER_OPTIONS },
  ],
  office: [
    { key: "floor", label: "طبقه چندم", type: "num" },
    { key: "area", label: "متراژ", type: "num" },
    { key: "rooms", label: "اتاق", type: "num" },
    { key: "kitchen", label: "آشپزخانه", type: "text" },
    { key: "units_per_floor", label: "واحد در طبقات", type: "num" },
    { key: "document_type", label: "سند", type: "text" },
    { key: "building_direction", label: "جهت", type: "pick", options: DIRECTION_OPTIONS },
    { key: "corner_type", label: "نبش", type: "pick", options: CORNER_OPTIONS },
  ],
};

/** Persian names for a property's extra_attrs keys. */
export const LEAD_ATTR_FA: Record<string, string> = Object.fromEntries(
  Object.values(LEAD_KIND_FIELDS).flat().map((f) => [f.key, f.label]),
);

/** Kinds a hand-entered lead can be (the create route's VALID_PROPERTY_KINDS). */
export const ADD_KINDS: Record<string, string> = { apartment: "آپارتمان", villa: "ویلایی", shop: "مغازه", office: "دفتر کار" };
