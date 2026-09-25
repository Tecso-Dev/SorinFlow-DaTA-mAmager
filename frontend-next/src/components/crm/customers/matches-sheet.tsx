"use client";

// «ملک‌های پیشنهادی»: what GET /crm/match/customer/{id} thinks fits this
// customer's budget, city and specs — a sheet so the customer list stays put
// behind it. The reasons line can arrive a moment after the score (the
// engine schedules one model call and caches it); reasons_pending polls a
// few times, the same way the old panel's call queue did.

import { AlertTriangle, ExternalLink, Phone, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Reveal, Tilt } from "@/components/viz";
import { Empty, ErrorNote, IsoBadge, ListSkeleton, ToneBadge } from "@/components/panel/kit";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { api } from "@/lib/api";
import { price } from "@/lib/crm";
import { faNum } from "@/lib/format";

type MatchItem = {
  id: number;
  serial_no: string | null;
  title: string;
  city_name: string | null;
  district: string | null;
  area: number | null;
  rooms: number | null;
  listing_type: string | null;
  price: number | null;
  deposit: number | null;
  rent_price: number | null;
  ai_summary: string | null;
  red_flags: string[];
  thumbnail_url: string | null;
  url: string | null;
  phone_number: string | null;
  score: number;
  reasons: string[];
  ai_reason?: string | null;
};

type MatchResponse = {
  items: MatchItem[];
  total: number;
  source: { id: number; name: string };
  reasons_pending: boolean;
};

function scoreTone(score: number): "success" | "warning" | "neutral" {
  if (score >= 75) return "success";
  if (score >= 50) return "warning";
  return "neutral";
}

export function CustomerMatchesSheet({
  open, onOpenChange, customerId, customerName,
}: { open: boolean; onOpenChange: (o: boolean) => void; customerId: number | null; customerName?: string }) {
  const [polls, setPolls] = useState(0);

  // a fresh customer (or a fresh open) starts the poll counter over —
  // adjusted during render rather than in an effect
  const resetKey = open ? customerId : null;
  const [lastResetKey, setLastResetKey] = useState(resetKey);
  if (resetKey !== lastResetKey) {
    setLastResetKey(resetKey);
    setPolls(0);
  }

  const query = useQuery({
    queryKey: ["crm", "match-customer", customerId],
    queryFn: () => api<MatchResponse>(`/crm/match/customer/${customerId}?limit=12`),
    enabled: open && customerId !== null,
    staleTime: 20_000,
  });

  useEffect(() => {
    if (!open || !query.data?.reasons_pending || polls >= 3) return;
    const t = setTimeout(() => {
      query.refetch();
      setPolls((c) => c + 1);
    }, 4000);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, query.data?.reasons_pending, polls]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full gap-0 p-0 sm:max-w-lg">
        <SheetHeader className="border-b px-5 py-4">
          <div className="flex items-center gap-3">
            <IsoBadge icon={Sparkles} className="size-11" />
            <div className="min-w-0">
              <SheetTitle className="text-base">ملک‌های پیشنهادی</SheetTitle>
              <SheetDescription className="truncate">برای {customerName ?? query.data?.source?.name ?? "مشتری"}</SheetDescription>
            </div>
          </div>
        </SheetHeader>
        <div className="flex-1 overflow-y-auto p-4">
          {query.isLoading && <ListSkeleton rows={4} />}
          {query.isError && <ErrorNote error={query.error} />}
          {query.data && query.data.items.length === 0 && (
            <Empty icon={Sparkles}>ملکی مطابق بودجه و درخواست این مشتری پیدا نشد.</Empty>
          )}
          <div className="grid gap-3">
            {query.data?.items.map((m, i) => (
              <Reveal key={m.id} delay={i * 0.03}>
                <Tilt max={3} className="block rounded-xl border bg-card p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-bold">{m.title}</p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {[m.city_name, m.district].filter(Boolean).join(" — ") || "—"}
                        {m.area ? ` · ${faNum(m.area)} متر` : ""}
                        {m.rooms ? ` · ${faNum(m.rooms)} خواب` : ""}
                      </p>
                    </div>
                    <ToneBadge tone={scoreTone(m.score)} className="shrink-0">{faNum(m.score)} امتیاز</ToneBadge>
                  </div>
                  <p className="mt-2 text-sm font-semibold tabular">
                    {m.listing_type === "rent"
                      ? `رهن ${price(m.deposit)} / اجاره ${price(m.rent_price)}`
                      : price(m.price)}
                  </p>
                  {m.ai_summary && <p className="mt-1.5 text-xs leading-6 text-muted-foreground">{m.ai_summary}</p>}
                  {m.ai_reason && (
                    <p className="mt-1.5 flex items-start gap-1 text-xs leading-6 text-primary">
                      <Sparkles className="mt-0.5 size-3 shrink-0" /> {m.ai_reason}
                    </p>
                  )}
                  {m.reasons.length > 0 && (
                    <ul className="mt-1.5 flex flex-wrap gap-1">
                      {m.reasons.map((r) => (
                        <li key={r} className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">{r}</li>
                      ))}
                    </ul>
                  )}
                  {m.red_flags.length > 0 && (
                    <p className="mt-1.5 flex items-start gap-1 text-xs leading-6 text-warning">
                      <AlertTriangle className="mt-0.5 size-3 shrink-0" /> {m.red_flags.join("، ")}
                    </p>
                  )}
                  <div className="mt-2.5 flex flex-wrap gap-2 text-xs">
                    {m.url && (
                      <a href={m.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">
                        <ExternalLink className="size-3.5" /> مشاهدهٔ آگهی
                      </a>
                    )}
                    {m.phone_number && (
                      <a href={`tel:${m.phone_number}`} className="inline-flex items-center gap-1 text-success hover:underline" dir="ltr">
                        <Phone className="size-3.5" /> {m.phone_number}
                      </a>
                    )}
                  </div>
                </Tilt>
              </Reveal>
            ))}
          </div>
          {query.data?.reasons_pending && polls < 3 && (
            <p className="mt-3 text-center text-[11px] text-muted-foreground">در حال آماده شدن دلیل هوش مصنوعی…</p>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
