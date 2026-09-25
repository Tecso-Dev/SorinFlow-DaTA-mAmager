"use client";

// «ارزان شدند»: listings whose price came down since the last scrape. A rent
// is compared as one figure (deposit + 30 × rent). A drop that now fits
// somebody's budget also lands in the matches card.

import { ArrowLeft, Check, Eye, Loader2, MoreHorizontal, Share2, TrendingDown, UserCheck, Zap } from "lucide-react";
import { useMutation, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { AnimatePresence } from "motion/react";
import { useState } from "react";
import { Empty, ErrorNote, ListSkeleton, Section, ToneBadge } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { api, ApiError } from "@/lib/api";
import { price } from "@/lib/crm";
import { faNum } from "@/lib/format";
import { can, useSession } from "@/lib/session";
import { ShareDialog } from "../leads/dialogs";
import { MatchDialog, type MatchTarget } from "../leads/match-dialog";
import { PropertySheet } from "../leads/property-sheet";
import { listingMoney, PhoneLink, SerialBadge, useIsSuper, when } from "../leads/shared";
import { CountPill, QueueItem, RefreshButton } from "./shared";

export type PriceDrop = {
  id: number; listing_type: string | null; from_amount: number; to_amount: number; delta_pct: number; moved_at: string | null;
  matches_created: number;
  property: {
    id: number; serial_no: number | null; title: string | null; city_name: string | null; district: string | null; area: number | null;
    rooms: number | null; listing_type: string | null; total_price: number | null; price: number | null; deposit: number | null;
    rent_price: number | null; phone_number: string | null;
  };
  lead: { id: number; status: string; assigned_to: string | null } | null;
};
export type DropsSummary = { new: number; min_drop_pct: number; every_minutes: number };

export function DropsCard({ list, summary }: { list: UseQueryResult<{ items: PriceDrop[]; total: number }>; summary?: DropsSummary }) {
  const qc = useQueryClient();
  const isSuper = useIsSuper();
  const filing = can(useSession().data?.user, { perm: "filing" });
  const [match, setMatch] = useState<MatchTarget | null>(null);
  const [prop, setProp] = useState<number | null>(null);
  const [share, setShare] = useState<number | null>(null);

  const seen = useMutation({
    mutationFn: (id: number) => api(`/crm/price-drops/${id}/decide`, { json: { status: "seen" } }),
    onSuccess: (_r, id) => {
      qc.setQueryData<{ items: PriceDrop[]; total: number }>(["crm", "calls", "drops"], (d) => (d ? { items: d.items.filter((a) => a.id !== id), total: Math.max(0, d.total - 1) } : d));
      setTimeout(() => qc.invalidateQueries({ queryKey: ["crm", "calls", "drops"] }), 400);
    },
    onError: (e) => toast.error("ثبت نشد", e instanceof ApiError ? e.message : undefined),
  });
  const run = useMutation({
    mutationFn: () => api<{ scanned?: number; drops?: number; matches?: number }>("/crm/price-drops/run", { method: "POST" }),
    onSuccess: (r) => {
      toast.success("بررسی شد", `${faNum(r.scanned ?? 0)} تغییر قیمت سنجیده شد، ${faNum(r.drops ?? 0)} کاهش، ${faNum(r.matches ?? 0)} تطبیق تازه`);
      qc.invalidateQueries({ queryKey: ["crm", "calls"] });
    },
    onError: (e) => toast.error("بررسی انجام نشد", e instanceof ApiError ? e.message : undefined),
  });

  const d = list.data;
  return (
    <Section
      title={<span className="flex items-center"><TrendingDown className="me-1.5 size-4 text-success" aria-hidden />ارزان شدند {d && <CountPill n={faNum(d.total)} />}</span>}
      hint={summary ? `هر ${faNum(summary.every_minutes)} دقیقه · کاهش ${faNum(summary.min_drop_pct)}٪ به بالا` : "هشدار کاهش قیمت"}
      action={
        <div className="flex items-center gap-1">
          {isSuper && (
            <Button size="sm" variant="outline" onClick={() => run.mutate()} disabled={run.isPending}>
              {run.isPending ? <Loader2 className="animate-spin" /> : <Zap />} بررسی الان
            </Button>
          )}
          <RefreshButton onClick={() => list.refetch()} spinning={list.isFetching} />
        </div>
      }
    >
      {list.isLoading ? (
        <ListSkeleton rows={2} />
      ) : list.isError ? (
        <ErrorNote error={list.error} />
      ) : !d?.items.length ? (
        <Empty icon={TrendingDown}>از آخرین بررسی، قیمتی پایین نیامده.</Empty>
      ) : (
        <ul className="grid gap-2.5" aria-label="کاهش قیمت‌ها">
          <AnimatePresence initial={false}>
            {d.items.map((a, i) => {
              const p = a.property;
              const rent = a.listing_type === "rent";
              const meta = [p.district || p.city_name, p.area ? `${faNum(p.area)} متر` : "", p.rooms !== null ? `${faNum(p.rooms)} خواب` : ""].filter(Boolean).join(" · ");
              return (
                <QueueItem key={a.id} i={i}>
                  <div className="flex items-start gap-3">
                    <div className="grid shrink-0 place-items-center rounded-xl bg-success/10 px-2.5 py-2 text-success">
                      <TrendingDown className="size-4" aria-hidden />
                      <span className="text-sm font-black tabular">{faNum(a.delta_pct)}٪</span>
                    </div>
                    <div className="min-w-0 flex-1">
                      <button type="button" onClick={() => setProp(p.id)} className="flex max-w-full items-center gap-1.5 text-start text-sm font-bold outline-none hover:text-primary focus-visible:text-primary">
                        <span className="truncate">{p.title || "آگهی"}</span>
                        <SerialBadge serial={p.serial_no} />
                      </button>
                      <p className="mt-0.5 text-xs text-muted-foreground">{meta}{a.moved_at ? ` · ${when(a.moved_at)}` : ""}</p>
                      <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-sm">
                        <span className="text-muted-foreground line-through decoration-destructive/60">{price(a.from_amount)}</span>
                        <ArrowLeft className="size-3.5 text-muted-foreground" aria-label="به" />
                        <span className="font-bold text-success">{price(a.to_amount)}</span>
                      </div>
                      {rent && <p className="text-[11px] text-muted-foreground">(رهن + ۳۰ × اجاره) — الان: {listingMoney(p)}</p>}
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {a.lead && <ToneBadge>لید #{faNum(a.lead.id)}{a.lead.assigned_to ? ` · ${a.lead.assigned_to}` : ""}</ToneBadge>}
                        {a.matches_created > 0 && <ToneBadge tone="info">{faNum(a.matches_created)} مشتری هم‌خوان</ToneBadge>}
                      </div>
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-1.5">
                    {p.phone_number && <PhoneLink phone={p.phone_number} className="me-auto rounded-lg bg-success/10 px-2.5 py-1.5 hover:no-underline" />}
                    <Button size="sm" variant="outline" onClick={() => setMatch({ kind: "customers", id: p.id })}><UserCheck /> متقاضیان هم‌خوان</Button>
                    <Button size="sm" variant="outline" onClick={() => seen.mutate(a.id)} disabled={seen.isPending && seen.variables === a.id}><Check /> دیدم</Button>
                    <DropdownMenu dir="rtl">
                      <DropdownMenuTrigger asChild>
                        <Button size="sm" variant="ghost" aria-label="کارهای بیشتر"><MoreHorizontal /></Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="start" className="w-48">
                        <DropdownMenuItem onSelect={() => setProp(p.id)}><Eye /> جزئیات</DropdownMenuItem>
                        {filing && <DropdownMenuItem onSelect={() => setShare(p.id)}><Share2 /> ارسال برای مشتری</DropdownMenuItem>}
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                </QueueItem>
              );
            })}
          </AnimatePresence>
        </ul>
      )}
      <MatchDialog target={match} onClose={() => setMatch(null)} />
      {prop !== null && <PropertySheet id={prop} onClose={() => setProp(null)} />}
      <ShareDialog propertyId={share} onClose={() => setShare(null)} />
    </Section>
  );
}
