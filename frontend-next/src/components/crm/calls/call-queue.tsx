"use client";

// The day's call list, one tap per outcome (POST /crm/leads/{id}/call).
// «پاسخ داد» asks for a line of what was said, «بازدید» for a Jalali date and
// time (the server books it in the calendar), «دوباره زنگ بزن» for when, and
// the two outcomes that close a lead ask first. A card slides out when its
// outcome is saved, and the list reloads.

import {
  AlarmClock, CalendarCheck, Check, ChevronDown, Coffee, Loader2, MessageSquareText, MoreHorizontal, PhoneCall,
  PhoneMissed, PhoneOff, RotateCcw, ThumbsDown, UserRound, XCircle,
} from "lucide-react";
import { useMutation, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { AnimatePresence } from "motion/react";
import { useState } from "react";
import { cn } from "cn";
import { JalaliDateInput } from "@/components/panel/date-input";
import { Empty, ErrorNote, Field, ListSkeleton, RingDialog, Section, ToneBadge, useConfirm } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import { price } from "@/lib/crm";
import { faNum } from "@/lib/format";
import { PhoneLink, SerialBadge, when } from "../leads/shared";
import type { Lead } from "../leads/types";
import { CountPill, QueueItem, RefreshButton } from "./shared";

export type CallsToday = {
  items: Lead[]; total: number; due_callbacks: number; done_today: number; agent: string; outcomes: Record<string, string>;
};
type CallResult = { label: string; next_call_at: string | null; event_id: number | null };
type Ask = { lead: Lead; mode: "answered" | "visit" | "callback" };

export function CallQueue({ q, onOpenLead }: { q: UseQueryResult<CallsToday>; onOpenLead: (id: number) => void }) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [ask, setAsk] = useState<Ask | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const send = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) => api<CallResult>(`/crm/leads/${id}/call`, { json: body }),
    onMutate: ({ id }) => setBusy(id),
    onSuccess: (r, { id, body }) => {
      // the card leaves at once; the list reloads a moment later
      qc.setQueryData<CallsToday>(["crm", "calls", "today"], (d) => (d ? { ...d, items: d.items.filter((l) => l.id !== id), total: Math.max(0, d.total - 1), done_today: d.done_today + 1 } : d));
      toast.success(r.label || "ثبت شد", r.event_id ? "در تقویم ثبت شد" : r.next_call_at ? `دوباره: ${when(r.next_call_at)}` : body.note ? "یادداشت روی لید ماند" : undefined);
      setTimeout(() => {
        qc.invalidateQueries({ queryKey: ["crm", "calls"] });
        qc.invalidateQueries({ queryKey: ["crm", "leads"] });
      }, 400);
    },
    onError: (e) => toast.error("ثبت نشد", e instanceof ApiError ? e.message : undefined),
    onSettled: () => setBusy(null),
  });
  const log = (id: number, body: Record<string, unknown>) => send.mutateAsync({ id, body }).then(() => true, () => false);

  async function closing(l: Lead, outcome: "not_interested" | "wrong_number") {
    const ok = await confirm({
      title: outcome === "wrong_number" ? "شماره اشتباه" : "علاقه ندارد",
      description: "این لید بسته می‌شود و دیگر در لیست تماس نمی‌آید.",
      confirm: "ثبت",
      danger: true,
      icon: XCircle,
    });
    if (ok) log(l.id, { outcome });
  }

  const d = q.data;
  return (
    <Section
      title={<span className="flex items-center">تماس‌های امروز من {d && <CountPill n={faNum(d.total)} />}</span>}
      hint={
        d
          ? `${faNum(d.done_today)} تماس امروز · ${faNum(d.due_callbacks)} تماس مجدد سررسیده · اولین تماس، لید را مال شما می‌کند`
          : "لیدهایی که نوبت تماسشان است"
      }
      action={<RefreshButton onClick={() => q.refetch()} spinning={q.isFetching} />}
    >
      {q.isLoading ? (
        <ListSkeleton rows={5} />
      ) : q.isError ? (
        <ErrorNote error={q.error} />
      ) : !d?.items.length ? (
        <Empty icon={Coffee}>
          فعلاً کسی منتظر تماس نیست.
          {!d?.total && <span className="mt-1 block text-xs">اسکرپ بعدی که تمام شود، لیدهای تازه اینجا می‌آیند.</span>}
        </Empty>
      ) : (
        <ul className="grid gap-2.5" aria-label="صف تماس">
          <AnimatePresence initial={false}>
            {d.items.map((l, i) => (
              <QueueItem key={l.id} i={i} className={cn(busy === l.id && "opacity-70")}>
                <CallCard
                  l={l}
                  busy={busy === l.id}
                  onOpen={() => onOpenLead(l.id)}
                  onAnswered={() => setAsk({ lead: l, mode: "answered" })}
                  onNoAnswer={() => log(l.id, { outcome: "no_answer" })}
                  onBusy={() => log(l.id, { outcome: "busy" })}
                  onVisit={() => setAsk({ lead: l, mode: "visit" })}
                  onCallback={() => setAsk({ lead: l, mode: "callback" })}
                  onClosing={(o) => closing(l, o)}
                />
              </QueueItem>
            ))}
          </AnimatePresence>
        </ul>
      )}
      <OutcomeDialog ask={ask} onClose={() => setAsk(null)} onSend={async (id, body) => (await log(id, body)) && setAsk(null)} pending={send.isPending} />
    </Section>
  );
}

function CallCard({
  l, busy, onOpen, onAnswered, onNoAnswer, onBusy, onVisit, onCallback, onClosing,
}: {
  l: Lead; busy: boolean; onOpen: () => void; onAnswered: () => void; onNoAnswer: () => void; onBusy: () => void;
  onVisit: () => void; onCallback: () => void; onClosing: (o: "not_interested" | "wrong_number") => void;
}) {
  const [notes, setNotes] = useState(false);
  const meta = [l.city_name, l.category_name, l.district, l.area ? `${faNum(l.area)} متر` : "", l.price ? price(l.price) : ""].filter(Boolean).join(" · ");
  return (
    <div className="grid gap-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            {l.next_call_at && <ToneBadge tone="warning"><RotateCcw className="size-3" aria-hidden /> تماس مجدد {when(l.next_call_at)}</ToneBadge>}
            {l.call_attempts ? <ToneBadge>{faNum(l.call_attempts)} تماس قبلی</ToneBadge> : <ToneBadge tone="success">اولین تماس</ToneBadge>}
            {l.assigned_to && <ToneBadge tone="primary"><UserRound className="size-3" aria-hidden />{l.assigned_to}</ToneBadge>}
            {l.contact_channel === "chat_only" && <ToneBadge tone="info">فقط چت</ToneBadge>}
          </div>
          <button type="button" onClick={onOpen} className="mt-1.5 block max-w-full truncate text-start text-[15px] font-bold outline-none hover:text-primary focus-visible:text-primary focus-visible:underline">
            {l.property_title || "بدون عنوان"}
          </button>
          <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            <SerialBadge serial={l.serial_no} />
            {meta}
          </div>
        </div>
        {l.phone_number && (
          <PhoneLink phone={l.phone_number} big className="shrink-0 self-start rounded-xl bg-success/10 px-3 py-2 hover:no-underline hover:bg-success/15" />
        )}
      </div>

      {l.notes && (
        <div className="rounded-lg bg-muted/50 px-3 py-2 text-xs leading-6 text-muted-foreground">
          <p className={cn("whitespace-pre-wrap", !notes && "line-clamp-2")}>{l.notes}</p>
          {l.notes.length > 120 && (
            <button type="button" onClick={() => setNotes(!notes)} className="mt-0.5 inline-flex items-center gap-0.5 font-semibold text-primary outline-none focus-visible:underline" aria-expanded={notes}>
              {notes ? "کمتر" : "بیشتر"} <ChevronDown className={cn("size-3 transition-transform", notes && "rotate-180")} aria-hidden />
            </button>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-1.5">
        <Button size="sm" onClick={onAnswered} disabled={busy}>
          {busy ? <Loader2 className="animate-spin" /> : <Check />} پاسخ داد
        </Button>
        <Button size="sm" variant="outline" onClick={onNoAnswer} disabled={busy}><PhoneMissed /> پاسخ نداد</Button>
        <Button size="sm" variant="outline" onClick={onCallback} disabled={busy} className="hidden sm:inline-flex"><AlarmClock /> دوباره زنگ بزن</Button>
        <Button size="sm" variant="outline" onClick={onVisit} disabled={busy} className="hidden sm:inline-flex"><CalendarCheck /> بازدید</Button>
        <DropdownMenu dir="rtl">
          <DropdownMenuTrigger asChild>
            <Button size="sm" variant="ghost" disabled={busy} aria-label="نتیجه‌های دیگر"><MoreHorizontal /> بیشتر</Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-48">
            <DropdownMenuItem className="sm:hidden" onSelect={onCallback}><AlarmClock /> دوباره زنگ بزن</DropdownMenuItem>
            <DropdownMenuItem className="sm:hidden" onSelect={onVisit}><CalendarCheck /> بازدید</DropdownMenuItem>
            <DropdownMenuItem onSelect={onBusy}><PhoneOff /> مشغول بود</DropdownMenuItem>
            <DropdownMenuItem onSelect={onOpen}><PhoneCall /> جزئیات لید</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onSelect={() => onClosing("not_interested")}><ThumbsDown /> علاقه ندارد</DropdownMenuItem>
            <DropdownMenuItem variant="destructive" onSelect={() => onClosing("wrong_number")}><XCircle /> شماره اشتباه</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}

/* ───────────────────────── the outcome dialogs ───────────────────────── */

const PRESETS = [
  { key: "1h", label: "یک ساعت دیگر" },
  { key: "3h", label: "سه ساعت دیگر" },
  { key: "tomorrow", label: "فردا ۱۰ صبح" },
  { key: "custom", label: "تاریخ و ساعت دیگر…" },
] as const;
type Preset = (typeof PRESETS)[number]["key"];

function presetTime(p: Preset): Date | null {
  const at = new Date();
  if (p === "1h") at.setHours(at.getHours() + 1);
  else if (p === "3h") at.setHours(at.getHours() + 3);
  else if (p === "tomorrow") {
    at.setDate(at.getDate() + 1);
    at.setHours(10, 0, 0, 0);
  } else return null;
  return at;
}

function OutcomeDialog({
  ask, onClose, onSend, pending,
}: { ask: Ask | null; onClose: () => void; onSend: (id: number, body: Record<string, unknown>) => void; pending: boolean }) {
  const [note, setNote] = useState("");
  const [at, setAt] = useState<Date | null>(null);
  const [preset, setPreset] = useState<Preset>("1h");
  const [forAsk, setForAsk] = useState<Ask | null>(null);
  if (ask !== forAsk) {
    setForAsk(ask);
    setNote("");
    setAt(null);
    setPreset("1h");
  }
  const mode = ask?.mode;
  const title = mode === "answered" ? "پاسخ داد" : mode === "visit" ? "بازدید گذاشتیم" : "دوباره زنگ بزن";
  const icon = mode === "answered" ? MessageSquareText : mode === "visit" ? CalendarCheck : AlarmClock;
  const description =
    mode === "answered"
      ? "چه گفت؟ یک خط کافی است؛ روی لید می‌ماند."
      : mode === "visit"
        ? "زمان بازدید را بگذارید تا در تقویم ثبت شود؛ اگر هنوز مشخص نیست خالی بماند."
        : "کِی دوباره زنگ بزنیم؟ به وقت تهران.";
  const callbackAt = preset === "custom" ? at : presetTime(preset);
  const ready = mode !== "callback" || !!callbackAt;

  function submit() {
    if (!ask) return;
    if (mode === "answered") onSend(ask.lead.id, { outcome: "answered", note: note.trim() || null });
    else if (mode === "visit") onSend(ask.lead.id, { outcome: "visit", ...(at ? { visit_at: at.toISOString() } : {}) });
    else if (callbackAt) onSend(ask.lead.id, { outcome: "callback", callback_at: callbackAt.toISOString() });
  }

  return (
    <RingDialog
      open={!!ask}
      onOpenChange={(o) => !o && onClose()}
      icon={icon}
      title={title}
      description={<>{ask?.lead.property_title && <b className="block text-foreground">{ask.lead.property_title}</b>}{description}</>}
      footer={
        <>
          <Button className="w-full" onClick={submit} disabled={!ready || pending}>
            {pending && <Loader2 className="animate-spin" />} ثبت
          </Button>
          <Button variant="ghost" className="w-full" onClick={onClose}>انصراف</Button>
        </>
      }
    >
      {mode === "answered" && (
        <Field label="یادداشت (اختیاری)" htmlFor="oc-note">
          <Textarea id="oc-note" rows={3} autoFocus value={note} onChange={(e) => setNote(e.target.value)} placeholder="مثلاً: قیمت قطعی ۲ میلیارد، هفتهٔ بعد خالی می‌شود" />
        </Field>
      )}
      {mode === "visit" && (
        <Field label="تاریخ و ساعت بازدید (اختیاری)" htmlFor="oc-visit">
          <JalaliDateInput id="oc-visit" withTime value={at} onChange={setAt} />
        </Field>
      )}
      {mode === "callback" && (
        <div className="grid gap-3">
          <div role="radiogroup" aria-label="زمان تماس مجدد" className="grid grid-cols-2 gap-2">
            {PRESETS.map((p) => (
              <button
                key={p.key}
                type="button"
                role="radio"
                aria-checked={preset === p.key}
                onClick={() => setPreset(p.key)}
                className={cn(
                  "rounded-xl border px-3 py-2.5 text-sm font-medium outline-none transition focus-visible:ring-2 focus-visible:ring-ring",
                  preset === p.key ? "border-primary bg-primary/10 text-primary" : "hover:bg-accent",
                )}
              >
                {p.label}
              </button>
            ))}
          </div>
          {preset === "custom" ? (
            <Field label="تاریخ و ساعت" htmlFor="oc-cb">
              <JalaliDateInput id="oc-cb" withTime value={at} onChange={setAt} placeholder="1405/07/05 16:30" />
            </Field>
          ) : (
            callbackAt && <p className="text-center text-xs text-muted-foreground">دوباره: {when(callbackAt.toISOString())}</p>
          )}
        </div>
      )}
    </RingDialog>
  );
}
