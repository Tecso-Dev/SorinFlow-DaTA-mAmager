"use client";

// One lead in the side drawer: who and what at the top, the actions a
// consultant takes on it, then three tabs — the listing's details, the
// follow-up form (status, owner, district, notes) and the activity timeline.

import {
  Bell, CalendarDays, CalendarPlus, ExternalLink, FolderInput, Handshake, History, Info, Loader2, Network,
  NotebookPen, Phone, PlusCircle, RefreshCw, Save, Share2, Trash2, UserRoundPen, type LucideIcon,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "motion/react";
import { useState } from "react";
import { Empty, ErrorNote, Field, LabelBadge, ListSkeleton, NativeSelect, ToneBadge } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import { LEAD_STATUS, LEAD_STATUS_ORDER, price } from "@/lib/crm";
import { faDate, faNum } from "@/lib/format";
import { can, useSession } from "@/lib/session";
import { BinderDialog, ShareDialog, VisitEventDialog } from "./dialogs";
import { useLeadActions } from "./lead-actions";
import { MatchDialog, type MatchTarget } from "./match-dialog";
import { PropertyDetails } from "./property-details";
import { AgencyBadge, day, DupBadge, PhoneWithCopy, SerialBadge } from "./shared";
import { SideSheet } from "./side-sheet";
import type { Activity, Lead } from "./types";

const ACTIVITY_ICON: Record<string, LucideIcon> = {
  status_change: RefreshCw, note: NotebookPen, created: PlusCircle, converted: Handshake, notified: Bell,
  call: Phone, event: CalendarDays,
};

export function LeadSheet({ id, onClose }: { id: number | null; onClose: () => void }) {
  const user = useSession().data?.user;
  const actions = useLeadActions();
  const [match, setMatch] = useState<MatchTarget | null>(null);
  const [share, setShare] = useState<number | null>(null);
  const [binder, setBinder] = useState<number | null>(null);
  const [visit, setVisit] = useState<Lead | null>(null);
  const q = useQuery({
    queryKey: ["crm", "lead", id],
    queryFn: () => api<Lead>(`/crm/leads/${id}`),
    enabled: id !== null,
    retry: false,
  });
  const l = q.data;
  const filing = can(user, { perm: "filing" });

  return (
    <>
      <SideSheet
        open={id !== null}
        onOpenChange={(o) => !o && onClose()}
        title={l?.property_title ?? "جزئیات لید"}
        header={
          l ? (
            <div className="grid gap-1.5 pe-2">
              <div className="flex flex-wrap items-center gap-1.5">
                <SerialBadge serial={l.serial_no} />
                <LabelBadge map={LEAD_STATUS} value={l.status} />
                <AgencyBadge p={l} />
                <DupBadge of={l.ai_duplicate_of} />
                {l.notified && <ToneBadge tone="success"><Bell className="size-3" aria-hidden /> اطلاع داده شد</ToneBadge>}
              </div>
              <h2 className="text-lg leading-7 font-black">{l.property_title || "بدون عنوان"}</h2>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                <span className="font-bold text-primary">{price(l.price)}</span>
                {l.phone_number && <PhoneWithCopy phone={l.phone_number} />}
              </div>
            </div>
          ) : undefined
        }
      >
        {q.isLoading ? (
          <ListSkeleton rows={6} />
        ) : q.isError ? (
          <ErrorNote error={q.error} />
        ) : l ? (
          <div className="grid gap-4">
            <div className="flex flex-wrap gap-2" role="toolbar" aria-label="کارهای این لید">
              {l.property_url && (
                <Button asChild variant="outline" size="sm">
                  <a href={l.property_url} target="_blank" rel="noopener noreferrer"><ExternalLink /> مشاهدهٔ آگهی</a>
                </Button>
              )}
              <Button variant="outline" size="sm" onClick={() => setMatch({ kind: "lead", id: l.id })}><Network /> ملک‌های مشابه</Button>
              <Button variant="outline" size="sm" onClick={() => actions.convert(l.id)}><Handshake /> تبدیل به معامله</Button>
              <Button variant="outline" size="sm" onClick={() => setVisit(l)}><CalendarPlus /> ثبت بازدید</Button>
              {filing && <Button variant="outline" size="sm" onClick={() => setBinder(l.property_id)}><FolderInput /> بایگانی در زونکن</Button>}
              {filing && <Button variant="outline" size="sm" onClick={() => setShare(l.property_id)}><Share2 /> ارسال برای مشتری</Button>}
              {!l.notified && (
                <Button variant="outline" size="sm" disabled={actions.notifying} onClick={() => actions.notify(l.id)}>
                  {actions.notifying ? <Loader2 className="animate-spin" /> : <Bell />} ارسال اطلاع
                </Button>
              )}
              <Button
                variant="destructive"
                size="sm"
                onClick={async () => {
                  if (await actions.remove(l.id)) onClose();
                }}
              >
                <Trash2 /> حذف
              </Button>
            </div>

            <Tabs defaultValue="details" dir="rtl">
              <TabsList className="w-full">
                <TabsTrigger value="details"><Info /> جزئیات</TabsTrigger>
                <TabsTrigger value="follow"><UserRoundPen /> پیگیری</TabsTrigger>
                <TabsTrigger value="history"><History /> تاریخچه</TabsTrigger>
              </TabsList>
              <TabsContent value="details" className="mt-2">
                <Facts l={l} />
                {l.property_detail && (
                  <div className="mt-3">
                    <PropertyDetails
                      p={l.property_detail}
                      invalidate={[["crm", "lead", l.id], ["crm", "leads"]]}
                      editable={can(user, { perm: "properties" })}
                    />
                  </div>
                )}
              </TabsContent>
              <TabsContent value="follow" className="mt-2">
                <FollowUp key={l.id + (l.updated_at ?? "")} l={l} />
              </TabsContent>
              <TabsContent value="history" className="mt-2">
                <Timeline id={l.id} />
              </TabsContent>
            </Tabs>
          </div>
        ) : null}
      </SideSheet>
      <MatchDialog target={match} onClose={() => setMatch(null)} />
      <ShareDialog propertyId={share} onClose={() => setShare(null)} />
      <BinderDialog propertyId={binder} onClose={() => setBinder(null)} />
      <VisitEventDialog lead={visit} onClose={() => setVisit(null)} />
    </>
  );
}

function Facts({ l }: { l: Lead }) {
  const rows: [string, React.ReactNode][] = [
    ["فروشنده", l.seller_name],
    ["شهر", [l.city_name, l.district].filter(Boolean).join(" · ")],
    ["قیمت", price(l.price)],
    ["قیمت هر متر", l.price_per_meter ? price(l.price_per_meter) : null],
    ["متراژ", l.area ? `${faNum(l.area)} متر` : null],
    ["نوع", l.listing_type === "buy" ? "خرید" : l.listing_type === "rent" ? "اجاره" : null],
    ["دسته‌بندی", l.category_name],
    ["مسئول پیگیری", l.assigned_to],
    ["تماس‌ها", l.call_attempts ? `${faNum(l.call_attempts)} بار${l.last_call_at ? ` · آخرین ${day(l.last_call_at)}` : ""}` : null],
    ["اطلاع‌رسانی", l.notified ? `بله${l.notification_channel ? ` (${l.notification_channel})` : ""}` : "خیر"],
    ["ثبت لید", day(l.created_at)],
    ["برداشت آگهی", day(l.scraped_at)],
  ];
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-3 rounded-xl border bg-background/40 p-3.5 sm:grid-cols-3">
      {rows.filter(([, v]) => v !== null && v !== undefined && v !== "" && v !== "—").map(([k, v]) => (
        <div key={k} className="min-w-0">
          <dt className="text-[11px] text-muted-foreground">{k}</dt>
          <dd className="mt-0.5 text-sm font-medium break-words">{v}</dd>
        </div>
      ))}
      {l.notes && (
        <div className="col-span-full">
          <dt className="text-[11px] text-muted-foreground">یادداشت</dt>
          <dd className="mt-0.5 text-sm leading-6 whitespace-pre-wrap">{l.notes}</dd>
        </div>
      )}
    </dl>
  );
}

function FollowUp({ l }: { l: Lead }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ status: l.status, assigned_to: l.assigned_to ?? "", district: l.district ?? "", notes: l.notes ?? "" });
  const dirty = f.status !== l.status || f.assigned_to !== (l.assigned_to ?? "") || f.district !== (l.district ?? "") || f.notes !== (l.notes ?? "");
  const save = useMutation({
    mutationFn: () => api<Lead>(`/crm/leads/${l.id}`, { method: "PATCH", json: f }),
    onSuccess: () => {
      toast.success("لید به‌روز شد");
      qc.invalidateQueries({ queryKey: ["crm", "lead", l.id] });
      qc.invalidateQueries({ queryKey: ["crm", "leads"] });
      qc.invalidateQueries({ queryKey: ["crm", "activity", "lead", l.id] });
    },
    onError: (e) => toast.error("ذخیره نشد", e instanceof ApiError ? e.message : undefined),
  });
  return (
    <form
      className="grid grid-cols-1 gap-3 rounded-xl border bg-background/40 p-3.5 sm:grid-cols-2"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <Field label="وضعیت CRM" htmlFor="lf-status">
        <NativeSelect id="lf-status" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>
          {LEAD_STATUS_ORDER.map((s) => <option key={s} value={s}>{LEAD_STATUS[s].label}</option>)}
        </NativeSelect>
      </Field>
      <Field label="مسئول پیگیری" htmlFor="lf-assigned">
        <Input id="lf-assigned" value={f.assigned_to} placeholder="نام مسئول…" onChange={(e) => setF({ ...f, assigned_to: e.target.value })} />
      </Field>
      <Field label="منطقه" htmlFor="lf-district" className="sm:col-span-2">
        <Input id="lf-district" value={f.district} placeholder="مثلاً: خیابان کاشانی" onChange={(e) => setF({ ...f, district: e.target.value })} />
      </Field>
      <Field label="یادداشت" htmlFor="lf-notes" className="sm:col-span-2">
        <Textarea id="lf-notes" rows={5} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} className="leading-7" />
      </Field>
      <div className="flex justify-end sm:col-span-2">
        <Button type="submit" disabled={!dirty || save.isPending} className="shadow-[0_8px_24px_-8px_rgb(99_102_241/0.8)]">
          {save.isPending ? <Loader2 className="animate-spin" /> : <Save />} ذخیره
        </Button>
      </div>
    </form>
  );
}

function Timeline({ id }: { id: number }) {
  const q = useQuery({
    queryKey: ["crm", "activity", "lead", id],
    queryFn: () => api<{ items: Activity[] }>(`/crm/activity/lead/${id}`),
  });
  if (q.isLoading) return <ListSkeleton rows={4} />;
  if (q.isError) return <ErrorNote error={q.error} />;
  const items = q.data?.items ?? [];
  if (!items.length) return <Empty icon={History}>هنوز فعالیتی ثبت نشده است.</Empty>;
  return (
    <ol className="relative grid gap-0 ps-1">
      {items.map((a, i) => {
        const Icon = ACTIVITY_ICON[a.action] ?? History;
        return (
          <motion.li
            key={a.id}
            initial={{ opacity: 0, x: 8 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: Math.min(i, 10) * 0.035 }}
            className="relative flex gap-3 pb-4 last:pb-0"
          >
            {i < items.length - 1 && <span aria-hidden className="absolute start-[15px] top-8 bottom-0 w-px bg-border" />}
            <span className="relative z-[1] grid size-8 shrink-0 place-items-center rounded-full border bg-card text-primary shadow-sm">
              <Icon className="size-4" aria-hidden />
            </span>
            <div className="min-w-0 pt-1">
              <p className="text-sm leading-6">{a.detail}</p>
              <p className="text-[11px] text-muted-foreground">
                {[a.actor, a.created_at ? faDate(new Date(a.created_at), { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
            </div>
          </motion.li>
        );
      })}
    </ol>
  );
}
