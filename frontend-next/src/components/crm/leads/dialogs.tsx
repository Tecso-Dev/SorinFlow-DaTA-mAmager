"use client";

// The action dialogs a lead and a listing share: send a customer-safe card
// (share), file into a binder, and book a visit in the calendar.

import { CalendarPlus, Copy, FolderInput, MessageSquareText, Send, Share2 } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { JalaliDateInput } from "@/components/panel/date-input";
import { Empty, ErrorNote, Field, ListSkeleton, NativeSelect, RingDialog } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import { faNum, parseDigits } from "@/lib/format";
import { PhotoStrip } from "./lightbox";
import type { Cabinet, Lead } from "./types";

const errText = (e: unknown) => (e instanceof ApiError ? e.message : undefined);

/** SMS parts the way the server counts them (crm.sms_segments): UCS-2,
 *  since Persian text is never GSM-7. */
export function smsSegments(text: string): number {
  const n = text.length;
  return n <= 70 ? 1 : Math.ceil(n / 67);
}

/* ───────────────────────── share ───────────────────────── */

type ShareCard = { text: string; images?: string[]; removed?: string[]; serial_no?: number | null };
const REMOVED_FA: Record<string, string> = {
  phone_number: "شماره مالک", seller_name: "نام مالک", owner_phone: "شماره ثبت‌کننده", url: "لینک آگهی", address: "آدرس دقیق",
};

/** «ارسال»: the customer-safe card of a listing (no owner phone, no link),
 *  to WhatsApp, Telegram, the clipboard or an SMS. */
export function ShareDialog({ propertyId, onClose }: { propertyId: number | null; onClose: () => void }) {
  const q = useQuery({
    queryKey: ["filing", "share", propertyId],
    queryFn: () => api<ShareCard>(`/filing/files/${propertyId}/share`),
    enabled: propertyId !== null,
    retry: false,
  });
  const [text, setText] = useState<string | null>(null);
  const [to, setTo] = useState("");
  const body = text ?? q.data?.text ?? "";
  const sms = useMutation({
    mutationFn: () => api("/crm/sms/send", { json: { to_number: parseDigits(to).trim(), message: body } }),
    onSuccess: () => {
      toast.success("پیامک ارسال شد");
      setTo("");
    },
    onError: (e) => toast.error("پیامک ارسال نشد", errText(e)),
  });
  const validTo = /^0?9\d{9}$/.test(parseDigits(to).replace(/\D/g, ""));
  const removed = (q.data?.removed ?? []).map((k) => REMOVED_FA[k] ?? k);
  const enc = encodeURIComponent(body);

  function close() {
    setText(null);
    setTo("");
    onClose();
  }

  return (
    <RingDialog open={propertyId !== null} onOpenChange={(o) => !o && close()} icon={Share2} title="ارسال برای مشتری" wide
      description="متن بدون شماره و نام مالک است؛ پیش از ارسال می‌توانید آن را ویرایش کنید.">
      {q.isLoading ? (
        <ListSkeleton rows={3} />
      ) : q.isError ? (
        <ErrorNote error={q.error} />
      ) : (
        <div className="grid gap-3">
          <Field label="متن" htmlFor="share-text" hint={removed.length ? `حذف شد: ${removed.join("، ")}` : "اطلاعات محرمانه‌ای برای حذف نبود."}>
            <Textarea id="share-text" rows={8} value={body} onChange={(e) => setText(e.target.value)} className="leading-7" />
          </Field>
          {!!q.data?.images?.length && <PhotoStrip images={q.data.images} size={72} />}
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <Button asChild variant="outline">
              <a href={`https://wa.me/?text=${enc}`} target="_blank" rel="noopener noreferrer"><Send /> واتساپ</a>
            </Button>
            <Button asChild variant="outline">
              <a href={`https://t.me/share/url?url=&text=${enc}`} target="_blank" rel="noopener noreferrer"><Send /> تلگرام</a>
            </Button>
            <Button
              variant="outline"
              onClick={() => navigator.clipboard?.writeText(body).then(() => toast.success("کپی شد", "متن آمادهٔ ارسال است"), () => toast.error("کپی نشد"))}
            >
              <Copy /> کپی متن
            </Button>
          </div>
          <div className="flex items-end gap-2 border-t pt-3">
            <Field label="پیامک به شماره" htmlFor="share-to" className="flex-1" error={to && !validTo ? "شمارهٔ موبایل معتبر نیست" : undefined}>
              <Input id="share-to" dir="ltr" inputMode="tel" placeholder="09123456789" value={to} onChange={(e) => setTo(e.target.value)} className="text-end tabular" />
            </Field>
            <Button onClick={() => sms.mutate()} disabled={!validTo || !body || sms.isPending}>
              <MessageSquareText /> ارسال پیامک
            </Button>
          </div>
        </div>
      )}
    </RingDialog>
  );
}

/* ───────────────────────── file into a binder ───────────────────────── */

/** «بایگانی در زونکن»: every box as «کمد › زونکن › پوشه». */
export function BinderDialog({ propertyId, onClose }: { propertyId: number | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [target, setTarget] = useState("");
  const q = useQuery({
    queryKey: ["filing", "cabinets"],
    queryFn: () => api<{ items: Cabinet[] }>("/filing/cabinets"),
    enabled: propertyId !== null,
  });
  const choices: [string, string][] = [];
  for (const c of q.data?.items ?? []) {
    for (const b of c.binders ?? []) {
      choices.push([String(b.id), `${c.name} › ${b.name}`]);
      for (const f of b.folders ?? []) choices.push([String(f.id), `${c.name} › ${b.name} › ${f.name}`]);
    }
  }
  const move = useMutation({
    mutationFn: () =>
      api<{ updated: number; skipped?: number }>("/filing/files/bulk", {
        json: { ids: [propertyId], action: "move", binder_id: target === "none" ? null : Number(target) },
      }),
    onSuccess: (r) => {
      const name = choices.find(([id]) => id === target)?.[1];
      toast.success(target === "none" ? "از زونکن خارج شد" : `به «${name ?? "زونکن"}» منتقل شد`,
        r.skipped ? `${faNum(r.skipped)} فایل تغییر نکرد` : undefined);
      qc.invalidateQueries({ queryKey: ["filing"] });
      setTarget("");
      onClose();
    },
    onError: (e) => toast.error("انتقال انجام نشد", errText(e)),
  });
  return (
    <RingDialog
      open={propertyId !== null}
      onOpenChange={(o) => !o && (setTarget(""), onClose())}
      icon={FolderInput}
      title="بایگانی در زونکن"
      description="این فایل به کدام زونکن یا پوشه برود؟"
      footer={
        choices.length ? (
          <>
            <Button className="w-full" disabled={!target || move.isPending} onClick={() => move.mutate()}>انتقال</Button>
            <Button variant="ghost" className="w-full" onClick={onClose}>انصراف</Button>
          </>
        ) : undefined
      }
    >
      {q.isLoading ? (
        <ListSkeleton rows={2} />
      ) : q.isError ? (
        <ErrorNote error={q.error} />
      ) : !choices.length ? (
        <Empty icon={FolderInput}>
          هنوز کمد و زونکنی ساخته نشده است.
          <Link href="/panel/crm/filing" className="mt-1 block font-semibold text-primary hover:underline">ساختن در «کمد و زونکن»</Link>
        </Empty>
      ) : (
        <Field label="مقصد" htmlFor="binder-target">
          <NativeSelect id="binder-target" value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">انتخاب کنید…</option>
            {choices.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            <option value="none">— خارج کردن از زونکن —</option>
          </NativeSelect>
        </Field>
      )}
    </RingDialog>
  );
}

/* ───────────────────────── book a visit ───────────────────────── */

const pad = (n: number) => String(n).padStart(2, "0");
/** The calendar takes a local wall-clock time, as the old panel sent it. */
export const isoLocal = (d: Date) =>
  `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:00`;

function tomorrowAt10() {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  d.setHours(10, 0, 0, 0);
  return d;
}

/** «ثبت بازدید»: a calendar event prefilled from the lead (owner = the lead's
 *  contact; the customer side is filled in by hand). */
export function VisitEventDialog({ lead, onClose }: { lead: Lead | null; onClose: () => void }) {
  const qc = useQueryClient();
  const p = lead?.property_detail;
  const initial = () => ({
    title: `بازدید — ${lead?.property_title || "ملک"}`,
    start: tomorrowAt10() as Date | null,
    location: p?.address || [p?.city_name ?? lead?.city_name, p?.district ?? lead?.district, p?.neighborhood].filter(Boolean).join("، "),
    owner_name: lead?.seller_name ?? "",
    owner_phone: lead?.phone_number ?? "",
    customer_name: "",
    customer_phone: "",
    description: "",
  });
  const [f, setF] = useState(initial);
  const [forId, setForId] = useState<number | null>(null);
  if (lead && lead.id !== forId) {
    setForId(lead.id);
    setF(initial());
  }
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((s) => ({ ...s, [k]: v }));
  const save = useMutation({
    mutationFn: () =>
      api("/crm/calendar", {
        json: {
          event_type: "visit",
          title: f.title.trim(),
          start_at: isoLocal(f.start!),
          location: f.location.trim() || null,
          owner_name: f.owner_name.trim() || null,
          owner_phone: parseDigits(f.owner_phone).trim() || null,
          customer_name: f.customer_name.trim() || null,
          customer_phone: parseDigits(f.customer_phone).trim() || null,
          description: f.description.trim() || null,
          lead_id: lead!.id,
          property_serial: lead!.serial_no ?? p?.serial_no ?? null,
        },
      }),
    onSuccess: () => {
      toast.success("بازدید در تقویم ثبت شد");
      qc.invalidateQueries({ queryKey: ["crm", "activity", "lead", lead!.id] });
      qc.invalidateQueries({ queryKey: ["crm", "calendar"] });
      onClose();
    },
    onError: (e) => toast.error("ثبت نشد", errText(e)),
  });
  return (
    <RingDialog
      open={!!lead}
      onOpenChange={(o) => !o && onClose()}
      icon={CalendarPlus}
      title="ثبت بازدید"
      description="قرار بازدید در تقویم ثبت می‌شود و در تاریخچهٔ لید می‌ماند."
      wide
      footer={
        <>
          <Button className="w-full" disabled={!f.title.trim() || !f.start || save.isPending} onClick={() => save.mutate()}>ثبت بازدید</Button>
          <Button variant="ghost" className="w-full" onClick={onClose}>انصراف</Button>
        </>
      }
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="عنوان" htmlFor="ve-title" className="sm:col-span-2">
          <Input id="ve-title" value={f.title} onChange={(e) => set("title", e.target.value)} />
        </Field>
        <Field label="زمان بازدید" htmlFor="ve-start">
          <JalaliDateInput id="ve-start" withTime value={f.start} onChange={(d) => set("start", d)} />
        </Field>
        <Field label="مکان" htmlFor="ve-loc">
          <Input id="ve-loc" value={f.location} onChange={(e) => set("location", e.target.value)} />
        </Field>
        <Field label="نام مالک" htmlFor="ve-on">
          <Input id="ve-on" value={f.owner_name} onChange={(e) => set("owner_name", e.target.value)} />
        </Field>
        <Field label="تلفن مالک" htmlFor="ve-op">
          <Input id="ve-op" dir="ltr" inputMode="tel" value={f.owner_phone} onChange={(e) => set("owner_phone", e.target.value)} className="text-end tabular" />
        </Field>
        <Field label="نام مشتری" htmlFor="ve-cn">
          <Input id="ve-cn" value={f.customer_name} onChange={(e) => set("customer_name", e.target.value)} />
        </Field>
        <Field label="تلفن مشتری" htmlFor="ve-cp">
          <Input id="ve-cp" dir="ltr" inputMode="tel" value={f.customer_phone} onChange={(e) => set("customer_phone", e.target.value)} className="text-end tabular" />
        </Field>
        <Field label="توضیحات" htmlFor="ve-desc" className="sm:col-span-2">
          <Textarea id="ve-desc" rows={2} value={f.description} onChange={(e) => set("description", e.target.value)} />
        </Field>
      </div>
    </RingDialog>
  );
}
