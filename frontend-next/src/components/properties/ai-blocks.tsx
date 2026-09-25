"use client";

// The two AI boxes inside the property detail sheet: «برداشت هوش مصنوعی»
// (the listing-text reader, any role can view, root/super_admin can reread)
// and «برچسب‌های هوش تصویری» (the vision tagger, root/super_admin only —
// gated on the client AND by the server's own 403, which the query below
// simply surfaces as an empty box for anyone else).

import { Loader2, RefreshCw, Sparkles } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "motion/react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { faNum, faPercent } from "@/lib/format";
import { can, useSession } from "@/lib/session";
import type { ListingFacts, PhotoTags, Property } from "./types";

const AI_PROPERTY_KIND_FA: Record<string, string> = {
  apartment: "آپارتمان", house: "خانه / ویلایی", land: "زمین", shop: "مغازه", office: "دفتر", other: "سایر",
};
const AI_AMENITIES: [keyof ListingFacts, string][] = [
  ["has_elevator", "آسانسور"], ["has_parking", "پارکینگ"], ["has_storage", "انباری"], ["has_balcony", "بالکن"],
];
const AI_DEAL_FLAGS: [keyof ListingFacts, string][] = [
  ["convertible", "قابل تبدیل"], ["exchange", "معاوضه"], ["vacant", "تخلیه"], ["negotiable", "قابل مذاکره"],
];

function Chip({ label, value, warn, off, conf }: { label?: string; value: string; warn?: boolean; off?: boolean; conf?: number }) {
  return (
    <span
      title={conf !== undefined && conf !== null ? `اطمینان ${faPercent(conf * 100, 0)}` : undefined}
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px]",
        warn ? "border-warning/30 bg-warning/10 text-warning" : off ? "border-transparent bg-muted text-muted-foreground" : "border-primary/20 bg-primary/8 text-primary",
      )}
    >
      {label && <span className="font-semibold">{label}:</span>}
      {value}
    </span>
  );
}

/** «برداشت هوش مصنوعی» — reads Property.ai_facts, which arrives on the
 *  property itself (no extra request), and offers «بازخوانی» to admins. */
export function AiFactsBlock({ property }: { property: Property }) {
  const user = useSession().data?.user;
  const qc = useQueryClient();
  const canReread = can(user, { roles: ["root", "super_admin"] });
  const facts = property.ai_facts as unknown as ListingFacts | null;

  const reread = useMutation({
    mutationFn: () => api<{ id: number; facts: ListingFacts }>(`/ai/reader/${property.id}`, { method: "POST" }),
    onSuccess: () => {
      toast.success("هوش مصنوعی", "آگهی دوباره خوانده شد");
      qc.invalidateQueries({ queryKey: ["properties", property.id] });
    },
    onError: (e) => toast.error("بازخوانی ناموفق بود", e instanceof ApiError ? e.message : undefined),
  });

  const chips: React.ReactNode[] = [];
  if (facts) {
    const c = facts.confidence || {};
    if (facts.kind) chips.push(<Chip key="kind" label="نوع" value={AI_PROPERTY_KIND_FA[facts.kind] ?? facts.kind} conf={c.kind} />);
    if (facts.floor !== null && facts.floor !== undefined) {
      const of = facts.total_floors !== null && facts.total_floors !== undefined ? ` از ${faNum(facts.total_floors)}` : "";
      chips.push(<Chip key="floor" label="طبقه" value={`${faNum(facts.floor)}${of}`} conf={c.floor} />);
    }
    if (facts.year_built) chips.push(<Chip key="year" label="ساخت" value={faNum(facts.year_built, { useGrouping: false })} conf={c.year_built} />);
    if (facts.document) chips.push(<Chip key="doc" label="سند" value={facts.document} conf={c.document} />);
    if (facts.condition) chips.push(<Chip key="cond" label="وضعیت" value={facts.condition} conf={c.condition} />);
    if (facts.district) chips.push(<Chip key="dist" label="منطقه" value={facts.district} conf={c.district} />);
    for (const [key, label] of AI_AMENITIES) {
      const v = facts[key];
      if (v === true) chips.push(<Chip key={key} value={label} conf={c[key as string]} />);
      else if (v === false) chips.push(<Chip key={key} value={`بدون ${label}`} off conf={c[key as string]} />);
    }
    for (const [key, label] of AI_DEAL_FLAGS) {
      if (facts[key] === true) chips.push(<Chip key={key} value={label} conf={c[key as string]} />);
    }
    if (facts.negotiable === false) chips.push(<Chip key="fixed" value="قیمت مقطوع" off conf={c.negotiable} />);
    for (const s of facts.suitable_for ?? []) chips.push(<Chip key={`s-${s}`} label="مناسب" value={s} />);
    for (const s of facts.red_flags ?? []) chips.push(<Chip key={`r-${s}`} value={`⚠ ${s}`} warn />);
  }

  const readAt = property.ai_read_at ? new Date(property.ai_read_at).toLocaleString("fa-IR") : "";
  const meta = [facts?.model ?? "", readAt].filter(Boolean).join(" · ");

  return (
    <section className="rounded-xl border bg-background/40 p-3.5">
      <div className="mb-2.5 flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-[13px] font-bold">
          <Sparkles className="size-4 text-primary" aria-hidden /> برداشت هوش مصنوعی
        </h3>
        {canReread && (
          <Button size="sm" variant="outline" disabled={reread.isPending} onClick={() => reread.mutate()}>
            {reread.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
            بازخوانی
          </Button>
        )}
      </div>
      {!facts ? (
        <p className="text-xs text-muted-foreground">هنوز خوانده نشده — خواننده هر دو دقیقه آگهی‌های تازه را می‌خواند.</p>
      ) : (
        <>
          {facts.summary && <p className="mb-2 text-sm leading-6">{facts.summary}</p>}
          <div className="flex flex-wrap gap-1.5">
            {chips.length ? chips : <span className="text-xs text-muted-foreground">متن آگهی چیزی بیش از فیلدهای خودش نمی‌گفت.</span>}
          </div>
          {meta && <div className="mt-2 text-[11px] text-muted-foreground">{meta}</div>}
        </>
      )}
    </section>
  );
}

/** «برچسب‌های هوش تصویری» — GET /ai/photo/{id}, root/super_admin only; the
 *  server 403s anyone else, and the block simply renders nothing for them. */
export function AiPhotoTagsBlock({ propertyId }: { propertyId: number }) {
  const user = useSession().data?.user;
  const allowed = can(user, { roles: ["root", "super_admin"] });
  const q = useQuery({
    queryKey: ["properties", propertyId, "ai-photo"],
    queryFn: () => api<PhotoTags>(`/ai/photo/${propertyId}`),
    enabled: allowed,
    retry: false,
  });
  if (!allowed) return null;
  return (
    <section className="rounded-xl border bg-background/40 p-3.5">
      <h3 className="mb-2.5 flex items-center gap-1.5 text-[13px] font-bold">
        <Sparkles className="size-4 text-primary" aria-hidden /> برچسب‌های هوش تصویری
      </h3>
      {q.isLoading ? (
        <div className="h-4 w-2/3 animate-pulse rounded bg-muted" aria-hidden />
      ) : q.isError ? (
        <p className="text-xs text-muted-foreground">در دسترس نیست.</p>
      ) : !q.data?.labels.length ? (
        <p className="text-xs text-muted-foreground">هنوز برچسب زده نشده.</p>
      ) : (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex flex-wrap gap-1.5">
          {q.data.labels.map((l) => <Chip key={l} value={l} />)}
        </motion.div>
      )}
      {q.data?.tagged_at && <div className="mt-2 text-[11px] text-muted-foreground">{new Date(q.data.tagged_at).toLocaleString("fa-IR")}</div>}
    </section>
  );
}
