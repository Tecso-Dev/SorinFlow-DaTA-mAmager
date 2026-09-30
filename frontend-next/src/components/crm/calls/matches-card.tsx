"use client";

// «مشتری‌هایی که دنبال آگهی‌های تازه بودند»: every new listing is scored
// against the customers' criteria as it arrives; the fits wait here, one card
// per customer × listing, strongest first. One call, one button.

import {
  Check, Crosshair, Eye, House, Loader2, MessageSquareText, MoreHorizontal, Share2, UserRound, X, Zap,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { AnimatePresence } from "motion/react";
import { useState } from "react";
import { Empty, ErrorNote, Field, ListSkeleton, RingDialog, Section, ToneBadge } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import { price, TEMPERATURE } from "@/lib/crm";
import { faNum } from "@/lib/format";
import { can, useSession } from "@/lib/session";
import { ShareDialog, smsSegments } from "../leads/dialogs";
import { PropertySheet } from "../leads/property-sheet";
import { listingMoney, PhoneLink, ScoreDial, SerialBadge, useIsSuper } from "../leads/shared";
import { CountPill, QueueItem, RefreshButton } from "./shared";

export type QueueMatch = {
  id: number;
  score: number;
  reasons: string[] | null;
  consultant: string | null;
  created_at: string | null;
  property: {
    id: number; serial_no: number | null; title: string | null; url: string | null; city_name: string | null; district: string | null;
    area: number | null; rooms: number | null; listing_type: string | null; price: number | null; deposit: number | null;
    rent_price: number | null; phone_number: string | null;
  } | null;
  customer: {
    id: number; full_name: string | null; mobile1: string | null; temperature: string | null; consultant_name: string | null;
    budget_max: number | null; desired_district: string | null; desired_specs: string | null;
  } | null;
};
export type MatchesSummary = { new: number; cursor: number; min_score: number; every_minutes: number };
type SmsPreview = { to: string; customer: string | null; text: string; segments: number };

export function MatchesCard({ list, summary }: { list: UseQueryResult<{ items: QueueMatch[]; total: number }>; summary?: MatchesSummary }) {
  const qc = useQueryClient();
  const isSuper = useIsSuper();
  const filing = can(useSession().data?.user, { perm: "filing" });
  const [note, setNote] = useState<QueueMatch | null>(null);
  const [sms, setSms] = useState<QueueMatch | null>(null);
  const [prop, setProp] = useState<number | null>(null);
  const [share, setShare] = useState<number | null>(null);

  const drop = (id: number) => {
    qc.setQueryData<{ items: QueueMatch[]; total: number }>(["crm", "calls", "matches"], (d) => (d ? { items: d.items.filter((m) => m.id !== id), total: Math.max(0, d.total - 1) } : d));
    setTimeout(() => qc.invalidateQueries({ queryKey: ["crm", "calls", "matches"] }), 400);
  };
  const decide = useMutation({
    mutationFn: ({ id, status, note }: { id: number; status: "contacted" | "dismissed"; note?: string | null }) =>
      api(`/crm/matches/${id}/decide`, { json: { status, note: note ?? null } }),
    onSuccess: (_r, v) => {
      toast.success("ثبت شد", v.status === "contacted" ? "در پروندهٔ مشتری نوشته شد" : undefined);
      drop(v.id);
      setNote(null);
    },
    onError: (e) => toast.error("ثبت نشد", e instanceof ApiError ? e.message : undefined),
  });
  const run = useMutation({
    mutationFn: () => api<{ scanned?: number; matched?: number }>("/crm/matches/run", { method: "POST" }),
    onSuccess: (r) => {
      toast.success("بررسی شد", `${faNum(r.scanned ?? 0)} آگهی سنجیده شد، ${faNum(r.matched ?? 0)} تطبیق تازه`);
      qc.invalidateQueries({ queryKey: ["crm", "calls"] });
    },
    onError: (e) => toast.error("بررسی انجام نشد", e instanceof ApiError ? e.message : undefined),
  });

  const d = list.data;
  return (
    <Section
      title={<span className="flex items-center"><Crosshair className="me-1.5 size-4 text-primary" aria-hidden />مشتری‌های هم‌خوان با آگهی‌های تازه {d && <CountPill n={faNum(d.total)} />}</span>}
      hint={summary ? `هر ${faNum(summary.every_minutes)} دقیقه · آستانهٔ ${faNum(summary.min_score)}٪` : "موتور تطبیق خودکار"}
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
        <ListSkeleton rows={3} />
      ) : list.isError ? (
        <ErrorNote error={list.error} />
      ) : !d?.items.length ? (
        <Empty icon={Crosshair}>
          فعلاً آگهی تازه‌ای با معیار مشتری‌ها نخوانده.
          {summary && !summary.cursor && <span className="mt-1 block text-xs">اولین اسکرپ که تمام شود، موتور تطبیق شروع می‌کند.</span>}
        </Empty>
      ) : (
        <ul className="grid gap-2.5" aria-label="تطبیق‌های تازه">
          <AnimatePresence initial={false}>
            {d.items.map((m, i) => {
              const p = m.property;
              const c = m.customer;
              const temp = c?.temperature ? TEMPERATURE[c.temperature] : null;
              const wants = [c?.desired_district, c?.desired_specs, c?.budget_max ? `تا ${price(c.budget_max)}` : ""].filter(Boolean).join(" · ");
              const meta = [p?.district || p?.city_name, p?.area ? `${faNum(p.area)} متر` : "", p?.rooms !== null && p?.rooms !== undefined ? `${faNum(p.rooms)} خواب` : "", p ? listingMoney(p) : ""].filter(Boolean).join(" · ");
              const pending = decide.isPending && decide.variables?.id === m.id;
              return (
                <QueueItem key={m.id} i={i}>
                  <div className="flex gap-3">
                    <ScoreDial score={m.score} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <UserRound className="size-4 text-muted-foreground" aria-hidden />
                        <span className="font-bold">{c?.full_name || "مشتری"}</span>
                        {temp && <ToneBadge tone={temp.tone}>{temp.label}</ToneBadge>}
                        {c?.consultant_name && <ToneBadge tone="primary">{c.consultant_name}</ToneBadge>}
                      </div>
                      <p className="mt-0.5 text-xs text-muted-foreground">می‌خواست: {wants || "—"}</p>
                      <button
                        type="button"
                        onClick={() => p && setProp(p.id)}
                        className="mt-2 flex max-w-full items-center gap-1.5 text-start text-sm font-semibold outline-none hover:text-primary focus-visible:text-primary"
                      >
                        <House className="size-4 shrink-0 text-primary" aria-hidden />
                        <span className="truncate">{p?.title || "آگهی"}</span>
                        <SerialBadge serial={p?.serial_no} />
                      </button>
                      <p className="mt-0.5 text-xs text-muted-foreground">{meta}</p>
                      {!!m.reasons?.length && (
                        <div className="mt-1.5 flex flex-wrap gap-1">
                          {m.reasons.map((r) => <span key={r} className="rounded-md bg-primary/8 px-1.5 py-0.5 text-[11px] text-primary">{r}</span>)}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-1.5">
                    {c?.mobile1 && <PhoneLink phone={c.mobile1} className="me-auto rounded-lg bg-success/10 px-2.5 py-1.5 hover:no-underline" />}
                    <Button size="sm" onClick={() => setNote(m)} disabled={pending}>
                      {pending ? <Loader2 className="animate-spin" /> : <Check />} تماس گرفتم
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => setSms(m)} disabled={!c?.mobile1} title={c?.mobile1 ? undefined : "مشتری شماره ندارد"}>
                      <MessageSquareText /> پیامک به مشتری
                    </Button>
                    <DropdownMenu dir="rtl">
                      <DropdownMenuTrigger asChild>
                        <Button size="icon-sm" variant="ghost" aria-label="کارهای بیشتر"><MoreHorizontal /></Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="start" className="w-48">
                        {p && <DropdownMenuItem onSelect={() => setProp(p.id)}><Eye /> جزئیات ملک</DropdownMenuItem>}
                        {p && filing && <DropdownMenuItem onSelect={() => setShare(p.id)}><Share2 /> ارسال</DropdownMenuItem>}
                        <DropdownMenuSeparator />
                        <DropdownMenuItem variant="destructive" onSelect={() => decide.mutate({ id: m.id, status: "dismissed" })}><X /> مناسب نیست</DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                </QueueItem>
              );
            })}
          </AnimatePresence>
        </ul>
      )}
      <ContactedDialog m={note} pending={decide.isPending} onClose={() => setNote(null)} onSave={(text) => note && decide.mutate({ id: note.id, status: "contacted", note: text || null })} />
      <SmsDialog m={sms} onClose={() => setSms(null)} onSent={(id) => { setSms(null); drop(id); }} />
      {prop !== null && <PropertySheet id={prop} onClose={() => setProp(null)} />}
      <ShareDialog propertyId={share} onClose={() => setShare(null)} />
    </Section>
  );
}

function ContactedDialog({ m, pending, onClose, onSave }: { m: QueueMatch | null; pending: boolean; onClose: () => void; onSave: (note: string) => void }) {
  const [text, setText] = useState("");
  const [forId, setForId] = useState<number | null>(null);
  if (m && m.id !== forId) {
    setForId(m.id);
    setText("");
  }
  return (
    <RingDialog
      open={!!m}
      onOpenChange={(o) => !o && onClose()}
      icon={Check}
      title="تماس گرفتم"
      description="نتیجه در پروندهٔ مشتری ثبت می‌شود."
      footer={
        <>
          <Button className="w-full" disabled={pending} onClick={() => onSave(text.trim())}>{pending && <Loader2 className="animate-spin" />} ثبت</Button>
          <Button variant="ghost" className="w-full" onClick={onClose}>انصراف</Button>
        </>
      }
    >
      <Field label="یادداشت (اختیاری)" htmlFor="mq-note">
        <Textarea id="mq-note" rows={3} autoFocus value={text} onChange={(e) => setText(e.target.value)} placeholder="مثلاً بازدید فردا ۱۰" />
      </Field>
    </RingDialog>
  );
}

/** «پیامک به مشتری»: the customer-safe card to the customer's own number,
 *  shown first so the consultant reads and edits what goes out. */
function SmsDialog({ m, onClose, onSent }: { m: QueueMatch | null; onClose: () => void; onSent: (id: number) => void }) {
  const pv = useQuery({
    queryKey: ["crm", "matches", "sms", m?.id],
    queryFn: () => api<SmsPreview>(`/crm/matches/${m!.id}/sms`),
    enabled: !!m,
    staleTime: 0,
    retry: false,
  });
  const [text, setText] = useState<string | null>(null);
  const [forId, setForId] = useState<number | null>(null);
  if ((m?.id ?? null) !== forId) {
    setForId(m?.id ?? null);
    setText(null);
  }
  const body = text ?? pv.data?.text ?? "";
  const send = useMutation({
    mutationFn: () => api<{ to: string; segments: number }>(`/crm/matches/${m!.id}/sms`, { json: { message: body } }),
    onSuccess: (r) => {
      toast.success("پیامک رفت", `به ${r.to} · ${faNum(r.segments)} بخش`);
      onSent(m!.id);
    },
    onError: (e) => toast.error("پیامک ارسال نشد", e instanceof ApiError ? e.message : undefined),
  });
  return (
    <RingDialog
      open={!!m}
      onOpenChange={(o) => !o && onClose()}
      icon={MessageSquareText}
      title="پیامک به مشتری"
      description={pv.data ? <>به <b dir="ltr" className="tabular">{pv.data.to}</b> ({pv.data.customer || "مشتری"}) فرستاده می‌شود.</> : "پیش‌نمایش متن…"}
      wide
      footer={
        <>
          <Button className="w-full" disabled={!body.trim() || send.isPending || !pv.data} onClick={() => send.mutate()}>
            {send.isPending ? <Loader2 className="animate-spin" /> : <MessageSquareText />} ارسال پیامک
          </Button>
          <Button variant="ghost" className="w-full" onClick={onClose}>انصراف</Button>
        </>
      }
    >
      {pv.isLoading ? (
        <ListSkeleton rows={3} />
      ) : pv.isError ? (
        <ErrorNote error={pv.error} />
      ) : (
        <Field label={`متن پیامک — ${faNum(body.length)} نویسه، ${faNum(smsSegments(body))} بخش`} htmlFor="mq-sms" error={!body.trim() ? "متن پیامک خالی است" : undefined}>
          <Textarea id="mq-sms" rows={8} value={body} onChange={(e) => setText(e.target.value)} className="leading-7" />
        </Field>
      )}
    </RingDialog>
  );
}
