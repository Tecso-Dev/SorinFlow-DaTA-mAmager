"use client";

// Small pieces the properties list, the detail sheet and the match dialog
// share: badges, safe links, and the property's money line.

import { Files, Phone } from "lucide-react";
import { cn } from "cn";
import { ToneBadge } from "@/components/panel/kit";
import { price } from "@/lib/crm";
import { faNum } from "@/lib/format";
import type { Property } from "./types";

/** کد ملک — grouping-free so it always matches what a person types into search. */
export function formatSerial(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return faNum(n, { useGrouping: false });
}

/** Only our own images and https ones ever reach an <img src>. */
export function safeImg(src: string): string | null {
  if (/^\/images\//.test(src)) return src;
  try {
    const u = new URL(src);
    return u.protocol === "https:" ? u.toString() : null;
  } catch {
    return null;
  }
}

/** Only http(s) links reach an href — never javascript: or another scheme. */
export function safeUrl(src: string | null | undefined): string | null {
  if (!src) return null;
  try {
    const u = new URL(src);
    return u.protocol === "http:" || u.protocol === "https:" ? u.toString() : null;
  } catch {
    return null;
  }
}

export const telHref = (phone: string) => `tel:${phone.replace(/[^\d+]/g, "")}`;

export function PhoneLink({ phone, className }: { phone: string; className?: string }) {
  return (
    <a
      href={telHref(phone)}
      dir="ltr"
      className={cn(
        "inline-flex items-center gap-1.5 font-bold tabular text-success outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
    >
      <Phone className="size-3.5" aria-hidden />
      {phone}
    </a>
  );
}

/**
 * A blank phone cell, in the three reasons that leave one blank — a poster
 * who hid the number, a scrape that hasn't come back with one yet, and a row
 * that predates the field entirely. Collapsing these to one «—» would erase
 * the difference between "will never have a phone" and "will get one on the
 * next scrape".
 */
export function NoPhoneCell({ p }: { p: Pick<Property, "contact_channel"> }) {
  if (p.contact_channel === "chat_only") return <ToneBadge tone="neutral">فقط چت</ToneBadge>;
  if (p.contact_channel === "unavailable") return <span className="text-xs text-warning">گرفته نشد</span>;
  return <span className="text-muted-foreground">—</span>;
}

/**
 * «املاکی»: the ad's own words say an agency posted it. Red when Divar's own
 * declared type disagrees (says «شخصی»), grey when the ad only says so itself.
 */
export function AgencyBadge({ p }: { p: Pick<Property, "agency_suspected" | "agency_evidence" | "advertiser_type"> }) {
  if (!p.agency_suspected) return null;
  const clash = p.advertiser_type === "personal";
  const ev = p.agency_evidence ? ` — «${p.agency_evidence}»` : "";
  const title = clash
    ? `متن آگهی می‌گوید مشاور املاک است${ev}؛ ولی دیوار آن را «شخصی» ثبت کرده`
    : `متن آگهی می‌گوید مشاور املاک است${ev}`;
  return (
    <span title={title}>
      <ToneBadge tone={clash ? "danger" : "neutral"}>املاکی</ToneBadge>
    </span>
  );
}

/** «احتمالاً تکراری» when the embeddings flagged this listing against an older one. */
export function DupBadge({ of, onOpen }: { of: number | null | undefined; onOpen?: (id: number) => void }) {
  if (!of) return null;
  return (
    <span title="متن این آگهی تقریباً همان آگهی دیگری است؛ فقط یک نشانه است و چیزی ادغام یا حذف نشده.">
      <ToneBadge tone="violet" className={onOpen ? "cursor-pointer" : undefined}>
        <Files className="size-3" aria-hidden />
        احتمالاً تکراری
        {onOpen && (
          <button
            type="button"
            className="underline decoration-dotted underline-offset-2 outline-none"
            onClick={(e) => {
              e.stopPropagation();
              onOpen(of);
            }}
          >
            اصل
          </button>
        )}
      </ToneBadge>
    </span>
  );
}

/** The money on a listing: a sale price, or a rent's two halves — matches formatPrice() everywhere it appears. */
export function listingMoney(p: Pick<Property, "listing_type" | "price" | "total_price" | "deposit" | "rent_price">): string {
  if (p.listing_type === "rent") {
    const parts = [p.deposit ? `رهن ${price(p.deposit)}` : "", p.rent_price ? `اجاره ${price(p.rent_price)}` : ""].filter(Boolean);
    return parts.join(" · ") || "—";
  }
  return price(p.total_price || p.price);
}

/** A match score in the old panel's three tiers (≥75 high, ≥50 mid, else low). */
export function scoreTone(score: number) {
  return score >= 75 ? "high" : score >= 50 ? "mid" : "low";
}

export function ScoreDial({ score, size = 46 }: { score: number; size?: number }) {
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
      <span className={cn("absolute inset-0 grid place-items-center text-xs font-black tabular", color)}>{faNum(score)}٪</span>
    </div>
  );
}
