"use client";

// «ملک‌های مشابه» — the properties-list detail sheet's own match modal
// (GET /crm/match/property/{id}). The Persian reason for each row is written
// by the model in the background; reasons_pending means it is still being
// written, so the list shows at once and a few light re-checks (every 4 s,
// at most three) pick the reasons up — the old panel's _pollMatchReasons.

import { Eye, Network, Sparkles } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { Empty, ErrorNote, ListSkeleton, RingDialog, ToneBadge } from "@/components/panel/kit";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { price } from "@/lib/crm";
import { faNum } from "@/lib/format";
import { DupBadge, formatSerial, PhoneLink, ScoreDial } from "./shared";
import type { MatchResult } from "./types";

/** The criteria the server actually filtered on, so an empty result explains itself. */
function criteria(src: MatchResult["source"] | undefined): string {
  if (!src) return "";
  const bits = [src.listing_type === "rent" ? "رهن و اجاره" : "خرید"];
  if (src.city_name) bits.push(src.city_name);
  if (src.district) bits.push(src.district);
  return bits.join(" • ");
}

export function MatchDialog({ propertyId, onClose, onOpenProperty }: { propertyId: number | null; onClose: () => void; onOpenProperty: (id: number) => void }) {
  const q = useQuery({
    queryKey: ["properties", "match", propertyId],
    queryFn: () => api<MatchResult>(`/crm/match/property/${propertyId}?limit=12`),
    enabled: propertyId !== null,
    staleTime: 0,
    retry: false,
    // a few light re-checks while the model writes the reasons, then give up quietly
    refetchInterval: (query) => (query.state.data?.reasons_pending && query.state.dataUpdateCount < 4 ? 4000 : false),
  });
  const items = q.data?.items ?? [];
  const pending = !!q.data?.reasons_pending;
  const src = q.data?.source;
  const crit = criteria(src);

  return (
    <RingDialog
      open={propertyId !== null}
      onOpenChange={(o) => !o && onClose()}
      icon={Network}
      title="ملک‌های مشابه"
      wide
      description={src?.title ? <>مبنای تطابق: <b>{src.title}</b>{items.length ? ` — ${faNum(items.length)} مورد` : ""}</> : undefined}
    >
      {src && src.listing_type && (src.price || src.deposit || src.rent_price) ? (
        <div className="mb-3 rounded-xl border bg-muted/40 px-3 py-2 text-xs leading-6 text-muted-foreground">
          {src.listing_type === "rent" ? (
            <>
              ودیعه {src.deposit ? price(src.deposit) : "—"} · اجاره {src.rent_price ? price(src.rent_price) : "ندارد"}
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
        <Empty icon={Network}>
          ملک مشابهی پیدا نشد.
          {crit && <span className="mt-1 block text-xs">جستجو بر اساس: {crit}</span>}
        </Empty>
      ) : (
        <div className="grid gap-3">
          {items.map((m, i) => (
            <MatchRow key={m.id} m={m} i={i} pending={pending} onOpen={onOpenProperty} />
          ))}
        </div>
      )}
    </RingDialog>
  );
}

function MatchRow({ m, i, pending, onOpen }: { m: MatchResult["items"][number]; i: number; pending: boolean; onOpen: (id: number) => void }) {
  const meta = [m.city_name, m.district, m.area ? `${faNum(m.area)} متر` : "", m.rooms !== null && m.rooms !== undefined ? `${faNum(m.rooms)} خواب` : ""]
    .filter(Boolean).join(" · ");
  const gap = m.price_gap_pct;
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: Math.min(i, 8) * 0.04 }}
      className="flex gap-3 rounded-xl border bg-background/50 p-3"
    >
      <ScoreDial score={m.score} />
      <div className="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="truncate font-bold" title={m.title}>{m.title}</span>
            <DupBadge of={m.ai_duplicate_of} onOpen={onOpen} />
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            <span className="font-mono tabular">{formatSerial(m.serial_no)}</span>
            {meta}
          </div>
          {!!m.reasons?.length && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {m.reasons.slice(0, 3).map((r) => (
                <span key={r} className="rounded-md bg-primary/8 px-1.5 py-0.5 text-[11px] text-primary">{r}</span>
              ))}
            </div>
          )}
          {m.ai_reason ? (
            <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-1.5 flex gap-1 text-xs leading-5 text-muted-foreground">
              <Sparkles className="mt-0.5 size-3.5 shrink-0 text-primary" aria-hidden /> {m.ai_reason}
            </motion.p>
          ) : pending ? (
            <div className="mt-2 h-3 w-2/3 animate-pulse rounded bg-muted" aria-hidden />
          ) : null}
        </div>
        <div className="flex shrink-0 flex-row flex-wrap items-center gap-2 sm:flex-col sm:items-end">
          {m.listing_type === "rent" ? (
            <div className="text-xs leading-5 sm:text-end">
              <div>ودیعه {m.deposit ? price(m.deposit) : "—"}</div>
              <div>اجاره {m.rent_price ? price(m.rent_price) : "بدون اجاره"}</div>
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
    </motion.div>
  );
}
