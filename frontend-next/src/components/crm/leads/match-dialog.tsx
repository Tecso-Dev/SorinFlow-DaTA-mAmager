"use client";

// «ملک‌های مشابه», «متقاضیان هم‌خوان» and «جستجوی معنایی» in one dialog.
// The Persian reason for each row is written by the model in the background
// (match_service._attach_reasons); `reasons_pending` means it is still being
// written, so the list shows at once and a few light re-checks (every 4 s, at
// most three) pick the reasons up, the old panel's _pollMatchReasons.

import { Eye, Map as Geo, MapPin, Network as Diagram3Icon, Sparkles, UserCheck as PersonCheck } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { useState } from "react";
import { cn } from "cn";
import { Empty, ErrorNote, ListSkeleton, RingDialog, ToneBadge } from "@/components/panel/kit";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { TEMPERATURE, price } from "@/lib/crm";
import { faNum } from "@/lib/format";
import { PropertySheet } from "./property-sheet";
import { DupBadge, PhoneLink, ScoreDial, SerialBadge } from "./shared";
import type { MatchedCustomer, MatchedListing, MatchResult } from "./types";

export type MatchTarget =
  | { kind: "lead"; id: number }
  | { kind: "property"; id: number }
  | { kind: "customers"; id: number }
  | { kind: "semantic"; text: string };

const TITLE: Record<MatchTarget["kind"], string> = {
  lead: "ملک‌های مشابه",
  property: "ملک‌های مشابه",
  customers: "متقاضیان هم‌خوان",
  semantic: "جستجوی معنایی",
};
const EMPTY: Record<MatchTarget["kind"], string> = {
  lead: "ملک مشابهی پیدا نشد؛ با اسکرپ بیشتر، نتایج بهتر می‌شود.",
  property: "ملک مشابهی پیدا نشد.",
  customers: "مشتری‌ای با این مشخصات دنبال ملک نبوده است.",
  semantic: "آگهی نزدیکی به این متن پیدا نشد؛ آگهی‌های تازه چند دقیقه بعد از رسیدن، بردار می‌گیرند.",
};

function fetchMatches(t: MatchTarget): Promise<MatchResult<MatchedListing | MatchedCustomer>> {
  switch (t.kind) {
    case "lead":
      return api(`/crm/match/lead/${t.id}?limit=12`);
    case "property":
      return api(`/crm/match/property/${t.id}?limit=12`);
    case "customers":
      return api(`/crm/match/property/${t.id}/customers?limit=12`);
    case "semantic":
      return api("/ai/embed/search", { json: { text: t.text, limit: 12 } });
  }
}

const isCustomer = (m: MatchedListing | MatchedCustomer): m is MatchedCustomer => "full_name" in m;

/** The criteria the server actually filtered on, so an empty list explains itself. */
function criteria(intent: MatchResult<unknown>["intent"]): string {
  if (!intent) return "";
  const family: Record<string, string> = { apartment: "آپارتمان", house: "ویلایی / خانه", land: "زمین", shop: "مغازه", office: "دفتر کار" };
  const bits = [intent.listing_type === "rent" ? "رهن و اجاره" : "خرید"];
  if (intent.family) bits.push(family[intent.family] ?? intent.family);
  if (intent.city) bits.push(intent.city);
  return bits.join(" • ");
}

export function MatchDialog({ target, onClose }: { target: MatchTarget | null; onClose: () => void }) {
  const [prop, setProp] = useState<number | null>(null);
  const key = target ? ["crm", "match", target.kind, "id" in target ? target.id : target.text] : ["crm", "match", "none"];
  const q = useQuery({
    queryKey: key,
    queryFn: () => fetchMatches(target!),
    enabled: !!target,
    staleTime: 0,
    retry: false,
    // a few light re-checks while the model writes the reasons, then give up quietly
    refetchInterval: (query) => (query.state.data?.reasons_pending && query.state.dataUpdateCount < 4 ? 4000 : false),
  });
  const items = q.data?.items ?? [];
  const src = q.data?.source;
  const pending = !!q.data?.reasons_pending;
  const crit = criteria(q.data?.intent);
  const grouped = items.some((m) => !isCustomer(m) && m.same_district !== undefined);
  const here = grouped ? items.filter((m) => !isCustomer(m) && m.same_district) : items;
  const there = grouped ? items.filter((m) => !isCustomer(m) && !m.same_district) : [];

  return (
    <>
      <RingDialog
        open={!!target}
        onOpenChange={(o) => !o && onClose()}
        icon={target?.kind === "customers" ? PersonCheck : target?.kind === "semantic" ? Sparkles : Diagram3Icon}
        title={target ? TITLE[target.kind] : ""}
        wide
        description={
          target?.kind === "semantic"
            ? <>نزدیک‌ترین آگهی‌ها به: «{target.text}»</>
            : src && (src.title || src.name)
              ? <>مبنای تطابق: <b>{src.title || src.name}</b>{items.length ? ` — ${faNum(items.length)} مورد` : ""}</>
              : undefined
        }
      >
        {src && src.listing_type && (src.price || src.deposit || src.rent_price) ? (
          <div className="mb-3 rounded-xl border bg-muted/40 px-3 py-2 text-xs leading-6 text-muted-foreground">
            {src.listing_type === "rent" ? (
              <>
                ودیعه {src.deposit ? price(src.deposit) : "—"} · اجاره {src.rent_price ? price(src.rent_price) : "ندارد"}
                {src.comparable ? <> — رهن کامل ≈ {price(src.comparable)}</> : null}
                <span className="block">اختلاف قیمت‌ها روی رهن کامل (ودیعه + ۳۰ × اجاره) حساب می‌شود.</span>
              </>
            ) : (
              <>قیمت {price(src.price)}</>
            )}
            {crit && <span className="block">{crit}</span>}
          </div>
        ) : null}
        {pending && (
          <p className="mb-2 flex items-center gap-1.5 text-xs text-muted-foreground" role="status">
            <Sparkles className="size-3.5 animate-pulse text-primary" aria-hidden /> دلیل هر پیشنهاد در حال نوشته شدن است…
          </p>
        )}
        {q.isLoading ? (
          <ListSkeleton rows={4} />
        ) : q.isError ? (
          <ErrorNote error={q.error} />
        ) : !items.length ? (
          <Empty icon={target?.kind === "customers" ? PersonCheck : Diagram3Icon}>
            {target ? EMPTY[target.kind] : ""}
            {crit && <span className="mt-1 block text-xs">جستجو بر اساس: {crit}</span>}
          </Empty>
        ) : (
          <div className="grid gap-3">
            {grouped && here.length > 0 && <GroupHead icon={MapPin} label={src?.district ?? "همین منطقه"} n={here.length} />}
            {here.map((m, i) => <MatchRow key={`${m.id}-${i}`} m={m} i={i} pending={pending} onOpen={setProp} />)}
            {there.length > 0 && <GroupHead icon={Geo} label={`مناطق دیگر${src?.city_name ? ` ${src.city_name}` : ""}`} n={there.length} />}
            {there.map((m, i) => <MatchRow key={`o${m.id}-${i}`} m={m} i={i + here.length} pending={pending} onOpen={setProp} />)}
          </div>
        )}
      </RingDialog>
      {/* mounted only while open: the sheet can open this dialog in turn */}
      {prop !== null && <PropertySheet id={prop} onClose={() => setProp(null)} />}
    </>
  );
}

function GroupHead({ icon: Icon, label, n }: { icon: React.ComponentType<{ className?: string }>; label: string; n: number }) {
  return (
    <div className="flex items-center gap-1.5 pt-1 text-xs font-bold text-muted-foreground">
      <Icon className="size-3.5 text-primary" />
      {label}
      <span className="font-normal">· {faNum(n)} مورد</span>
    </div>
  );
}

function MatchRow({ m, i, pending, onOpen }: { m: MatchedListing | MatchedCustomer; i: number; pending: boolean; onOpen: (id: number) => void }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: Math.min(i, 8) * 0.04 }}
      className="flex gap-3 rounded-xl border bg-background/50 p-3"
    >
      <ScoreDial score={m.score} />
      {isCustomer(m) ? <CustomerBody m={m} pending={pending} /> : <ListingBody m={m} pending={pending} onOpen={onOpen} />}
    </motion.div>
  );
}

function Reasons({ reasons, ai, pending }: { reasons: string[] | null; ai?: string | null; pending: boolean }) {
  return (
    <>
      {!!reasons?.length && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {reasons.slice(0, 3).map((r) => (
            <span key={r} className="rounded-md bg-primary/8 px-1.5 py-0.5 text-[11px] text-primary">{r}</span>
          ))}
        </div>
      )}
      {ai ? (
        <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-1.5 flex gap-1 text-xs leading-5 text-muted-foreground">
          <Sparkles className="mt-0.5 size-3.5 shrink-0 text-primary" aria-hidden />
          {ai}
        </motion.p>
      ) : pending ? (
        <div className="mt-2 h-3 w-2/3 animate-pulse rounded bg-muted" aria-hidden />
      ) : null}
    </>
  );
}

function ListingBody({ m, pending, onOpen }: { m: MatchedListing; pending: boolean; onOpen: (id: number) => void }) {
  const meta = [m.city_name, m.district, m.area ? `${faNum(m.area)} متر` : "", m.rooms !== null && m.rooms !== undefined ? `${faNum(m.rooms)} خواب` : ""].filter(Boolean).join(" · ");
  const gap = m.price_gap_pct;
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="truncate font-bold" title={m.title}>{m.title}</span>
          <DupBadge of={m.ai_duplicate_of} />
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          <SerialBadge serial={m.serial_no} />
          {meta}
        </div>
        <Reasons reasons={m.reasons} ai={m.ai_reason} pending={pending} />
      </div>
      <div className="flex shrink-0 flex-row flex-wrap items-center gap-2 sm:flex-col sm:items-end">
        {m.listing_type === "rent" ? (
          <div className="text-xs leading-5 sm:text-end">
            <div>ودیعه {m.deposit ? price(m.deposit) : "—"}</div>
            <div>اجاره {m.rent_price ? price(m.rent_price) : "بدون اجاره"}</div>
            {m.comparable ? <div className="text-muted-foreground" title="ودیعه + ۳۰ × اجارهٔ ماهانه">رهن کامل ≈ {price(m.comparable)}</div> : null}
          </div>
        ) : (
          <div className="text-sm font-bold">{m.price ? price(m.price) : "توافقی"}</div>
        )}
        {gap !== null && gap !== undefined && (
          <ToneBadge tone={gap <= 15 ? "success" : "warning"}>
            {gap === 0 ? "همین قیمت" : `${faNum(gap)}٪ ${m.price_direction === "higher" ? "گران‌تر" : "ارزان‌تر"}`}
          </ToneBadge>
        )}
        <div className="flex items-center gap-1.5">
          <Button size="sm" variant="outline" onClick={() => onOpen(m.id)}>
            <Eye className="size-3.5" /> جزئیات
          </Button>
          {m.phone_number && <PhoneLink phone={m.phone_number} className="text-xs" />}
        </div>
      </div>
    </div>
  );
}

function CustomerBody({ m, pending }: { m: MatchedCustomer; pending: boolean }) {
  const meta = [m.desired_district, m.desired_specs, m.consultant_name ? `مشاور: ${m.consultant_name}` : ""].filter(Boolean).join(" · ");
  const phone = m.mobile1 || m.mobile2;
  const temp = m.temperature ? TEMPERATURE[m.temperature] : null;
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-bold">{m.full_name}</span>
          {temp && <ToneBadge tone={temp.tone}>{temp.label}</ToneBadge>}
        </div>
        {meta && <div className="mt-0.5 text-xs text-muted-foreground">{meta}</div>}
        <Reasons reasons={m.reasons} ai={m.ai_reason} pending={pending} />
      </div>
      <div className={cn("flex shrink-0 items-center gap-2 sm:flex-col sm:items-end")}>
        <div className="text-sm font-bold">{m.budget_max ? `تا ${price(m.budget_max)}` : "—"}</div>
        {phone && <PhoneLink phone={phone} className="text-xs" />}
      </div>
    </div>
  );
}

