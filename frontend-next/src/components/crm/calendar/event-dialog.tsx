"use client";

// The full قرار (appointment) dialog: every CalendarEventIn field, the OTP
// dialog's look via RingDialog. Task and reminder rows never open this —
// the grid keeps them read-only (see calendar-page.tsx).

import { CalendarPlus, MessageSquareText, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Field, NativeSelect, RingDialog, useConfirm } from "@/components/panel/kit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { JalaliDateInput } from "@/components/panel/date-input";
import { formatJalali } from "@/lib/jalali";
import { faNum } from "@/lib/format";
import { EVENT_TYPES, localIso, REMIND_OPTIONS, type CalendarRow } from "./types";

type Draft = Partial<CalendarRow> & { start_at?: string | null };

type Form = {
  title: string;
  event_type: string;
  date: Date | null;
  time: string;      // "HH:MM"
  endDate: Date | null;
  endTime: string;
  all_day: boolean;
  location: string;
  owner_name: string;
  owner_phone: string;
  customer_name: string;
  customer_phone: string;
  assigned_to: string;
  agent_phone: string;
  property_serial: string;
  lead_id: string;
  customer_id: string;
  contact_id: string;
  deal_id: string;
  description: string;
  outcome: string;
  status: string;
  remind_before: number;
  sms_reminder: boolean;
};

const EMPTY: Form = {
  title: "", event_type: "visit", date: new Date(), time: "10:00", endDate: null, endTime: "",
  all_day: false, location: "", owner_name: "", owner_phone: "", customer_name: "", customer_phone: "",
  assigned_to: "", agent_phone: "", property_serial: "", lead_id: "", customer_id: "", contact_id: "",
  deal_id: "", description: "", outcome: "", status: "scheduled", remind_before: 60, sms_reminder: false,
};

function fromRow(row: CalendarRow): Form {
  const start = row.start_at ? new Date(row.start_at) : new Date();
  const end = row.end_at ? new Date(row.end_at) : null;
  const hm = (d: Date) => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return {
    title: row.title ?? "", event_type: row.event_type ?? "visit", date: start, time: hm(start),
    endDate: end, endTime: end ? hm(end) : "", all_day: !!row.all_day, location: row.location ?? "",
    owner_name: row.owner_name ?? "", owner_phone: row.owner_phone ?? "", customer_name: row.customer_name ?? "",
    customer_phone: row.customer_phone ?? "", assigned_to: row.assigned_to ?? "", agent_phone: row.agent_phone ?? "",
    property_serial: row.property_serial ? String(row.property_serial) : "",
    lead_id: row.lead_id ? String(row.lead_id) : "", customer_id: row.customer_id ? String(row.customer_id) : "",
    contact_id: row.contact_id ? String(row.contact_id) : "", deal_id: row.deal_id ? String(row.deal_id) : "",
    description: row.description ?? "", outcome: row.outcome ?? "", status: row.status || "scheduled",
    remind_before: row.remind_before ?? 60, sms_reminder: !!row.sms_reminder,
  };
}

function fromDraft(draft: Draft): Form {
  const start = draft.start_at ? new Date(draft.start_at) : new Date();
  return {
    ...EMPTY,
    title: draft.title ?? "", event_type: draft.event_type ?? "visit", date: start,
    time: `${String(start.getHours()).padStart(2, "0")}:${String(start.getMinutes()).padStart(2, "0")}`,
    location: draft.location ?? "", owner_name: draft.owner_name ?? "", owner_phone: draft.owner_phone ?? "",
    property_serial: draft.property_serial ? String(draft.property_serial) : "",
    lead_id: draft.lead_id ? String(draft.lead_id) : "", customer_id: draft.customer_id ? String(draft.customer_id) : "",
  };
}

/** Who gets the appointment's SMS: مالک / مشتری / کارشناس فروش, minus blanks
 *  and repeats — the same rule the server applies (CalendarEvent.sms_targets). */
function recipients(f: Form): { role: string; phone: string }[] {
  const rows = [
    { role: "مالک", phone: f.owner_phone.trim() },
    { role: "مشتری", phone: f.customer_phone.trim() },
    { role: "کارشناس فروش", phone: f.agent_phone.trim() },
  ];
  const seen = new Set<string>();
  return rows.filter((r) => {
    const digits = r.phone.replace(/\D/g, "");
    if (!digits || seen.has(digits)) return false;
    seen.add(digits);
    return true;
  });
}

const REMIND_FA: Record<number, string> = { 0: "", 15: "۱۵ دقیقه", 30: "۳۰ دقیقه", 60: "۱ ساعت", 180: "۳ ساعت", 1440: "۱ روز" };

export function EventDialog({
  open, onOpenChange, eventId, draft, onSaved, onDeleted,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  /** an id to edit, or undefined/null to create */
  eventId?: number | null;
  /** prefilled fields for a new event (clicking a day, «ثبت بازدید», …) */
  draft?: Draft;
  onSaved: () => void;
  onDeleted: () => void;
}) {
  const editing = !!eventId;
  // The caller remounts this dialog (a fresh `key`) every time it opens, so
  // its initial state can read straight off the props instead of an effect
  // resetting them — an editing dialog starts empty and loading, a new one
  // starts filled with its draft.
  const [form, setForm] = useState<Form>(() => (eventId ? EMPTY : fromDraft(draft ?? {})));
  const [loading, setLoading] = useState(() => !!eventId);
  const [saving, setSaving] = useState(false);
  const [smsSent, setSmsSent] = useState(false);
  const [lookup, setLookup] = useState<{ serial_no: number; title: string; location: string | null } | "notfound" | null>(null);
  const [lookingUp, setLookingUp] = useState(false);
  const confirm = useConfirm();
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => ({ ...f, [k]: v }));

  useEffect(() => {
    if (!open || !eventId) return;
    api<CalendarRow>(`/crm/calendar/${eventId}`)
      .then((row) => {
        setForm(fromRow(row));
        setSmsSent(!!row.sms_sent);
      })
      .catch((e) => toast.error("قرار یافت نشد", e instanceof ApiError ? e.message : undefined))
      .finally(() => setLoading(false));
  }, [open, eventId]);

  // «کد ملک» → آدرس, live: a debounced GET, autofilling the location only
  // while the person has not typed one of their own.
  const lookupTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (lookupTimer.current) clearTimeout(lookupTimer.current);
    const serial = form.property_serial.trim();
    if (!serial) return;   // the render ignores a stale `lookup` once the field is empty
    lookupTimer.current = setTimeout(async () => {
      setLookingUp(true);
      try {
        const r = await api<{ id: number; serial_no: number; title: string; location: string | null }>(
          `/crm/calendar/property-lookup/${encodeURIComponent(serial)}`,
        );
        setLookup(r);
        setForm((f) => (f.location.trim() ? f : { ...f, location: r.location ?? f.location }));
      } catch {
        setLookup("notfound");
      } finally {
        setLookingUp(false);
      }
    }, 450);
    return () => {
      if (lookupTimer.current) clearTimeout(lookupTimer.current);
    };
  }, [form.property_serial]);

  function body(): Record<string, unknown> | null {
    if (!form.title.trim()) {
      toast.error("عنوان قرار الزامی است");
      return null;
    }
    if (!form.date) {
      toast.error("تاریخ نامعتبر است");
      return null;
    }
    const start = new Date(form.date);
    if (form.all_day) start.setHours(0, 0, 0, 0);
    else {
      const [h, m] = (form.time || "10:00").split(":").map(Number);
      start.setHours(h || 0, m || 0, 0, 0);
    }
    let end: Date | null = null;
    if (form.endDate) {
      end = new Date(form.endDate);
      if (form.all_day) end.setHours(0, 0, 0, 0);
      else {
        const [h, m] = (form.endTime || "10:00").split(":").map(Number);
        end.setHours(h || 0, m || 0, 0, 0);
      }
    }
    const num = (v: string) => (v.trim() ? parseInt(v, 10) : null);
    return {
      title: form.title.trim(),
      event_type: form.event_type || "visit",
      start_at: localIso(start),
      end_at: end ? localIso(end) : null,
      all_day: form.all_day,
      location: form.location.trim() || null,
      owner_name: form.owner_name.trim() || null,
      owner_phone: form.owner_phone.trim() || null,
      customer_name: form.customer_name.trim() || null,
      customer_phone: form.customer_phone.trim() || null,
      assigned_to: form.assigned_to.trim() || null,
      agent_phone: form.agent_phone.trim() || null,
      property_serial: num(form.property_serial),
      lead_id: num(form.lead_id),
      customer_id: num(form.customer_id),
      contact_id: num(form.contact_id),
      deal_id: num(form.deal_id),
      description: form.description.trim() || null,
      outcome: form.outcome.trim() || null,
      status: editing ? form.status || "scheduled" : undefined,
      remind_before: form.remind_before,
      sms_reminder: form.sms_reminder,
    };
  }

  async function save() {
    const b = body();
    if (!b) return;
    setSaving(true);
    try {
      if (editing) {
        await api(`/crm/calendar/${eventId}`, { method: "PATCH", json: b });
        toast.success("قرار به‌روزرسانی شد");
      } else {
        await api("/crm/calendar", { json: b });
        toast.success("قرار ثبت شد");
      }
      onOpenChange(false);
      onSaved();
    } catch (e) {
      toast.error("ذخیره نشد", e instanceof ApiError ? e.message : undefined);
    } finally {
      setSaving(false);
    }
  }

  async function sendSmsNow() {
    if (!eventId) return;
    const to = recipients(form);
    if (!to.length) {
      toast.error("هیچ شماره‌ای برای این قرار وارد نشده است");
      return;
    }
    const who = to.map((r) => `${r.role} (${r.phone})`).join("، ");
    if (!(await confirm({
      title: "ارسال پیامک این قرار؟", icon: MessageSquareText,
      description: `برای ${faNum(to.length)} نفر ارسال می‌شود: ${who}`,
      confirm: "ارسال",
    }))) return;
    setSaving(true);
    try {
      const b = body();
      if (b) await api(`/crm/calendar/${eventId}`, { method: "PATCH", json: b });
      const r = await api<{ sent: unknown[]; failed: unknown[] }>(`/crm/calendar/${eventId}/sms`, { json: {} });
      const ok = r.sent.length, bad = r.failed.length;
      if (bad) toast.info("ناقص", `${faNum(ok)} پیامک ارسال شد، ${faNum(bad)} ناموفق`);
      else toast.success("موفق", `پیامک برای ${faNum(ok)} نفر ارسال شد`);
      setSmsSent(true);
    } catch (e) {
      toast.error("ارسال نشد", e instanceof ApiError ? e.message : undefined);
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    if (!eventId) return;
    if (!(await confirm({ title: "این قرار حذف شود؟", icon: Trash2, danger: true, confirm: "حذف" }))) return;
    setSaving(true);
    try {
      await api(`/crm/calendar/${eventId}`, { method: "DELETE" });
      toast.success("قرار حذف شد");
      onOpenChange(false);
      onDeleted();
    } catch (e) {
      toast.error("حذف نشد", e instanceof ApiError ? e.message : undefined);
    } finally {
      setSaving(false);
    }
  }

  const to = recipients(form);
  const smsHint = !form.sms_reminder
    ? ""
    : !to.length
      ? "هیچ شماره‌ای وارد نشده — پیامکی ارسال نمی‌شود."
      : form.remind_before === 0
        ? "یادآوری روی «بدون یادآوری» است — زمان ارسال را انتخاب کنید."
        : smsSent
          ? "پیامک این قرار قبلاً ارسال شده است."
          : `${REMIND_FA[form.remind_before] ?? `${faNum(form.remind_before)} دقیقه`} قبل از قرار به ${faNum(to.length)} نفر پیامک می‌رود: ${to.map((r) => `${r.role} (${r.phone})`).join("، ")}`;

  return (
    <RingDialog
      open={open}
      onOpenChange={onOpenChange}
      icon={CalendarPlus}
      wide
      title={editing ? "ویرایش قرار" : "قرار جدید"}
      description={loading ? "در حال بارگیری…" : undefined}
      footer={
        <>
          <Button className="w-full" disabled={saving || loading} onClick={save}>
            {editing ? "ذخیرهٔ تغییرات" : "ثبت قرار"}
          </Button>
          {editing && (
            <div className="grid w-full grid-cols-2 gap-2">
              <Button variant="outline" disabled={saving || loading} onClick={sendSmsNow} className="gap-1.5">
                <MessageSquareText className="size-4" /> ارسال پیامک الان
              </Button>
              <Button variant="destructive" disabled={saving || loading} onClick={remove} className="gap-1.5">
                <Trash2 className="size-4" /> حذف
              </Button>
            </div>
          )}
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="عنوان قرار" htmlFor="ev-title" className="sm:col-span-2">
          <Input id="ev-title" value={form.title} onChange={(e) => set("title", e.target.value)} maxLength={500} />
        </Field>

        <Field label="نوع قرار" htmlFor="ev-type">
          <NativeSelect id="ev-type" value={form.event_type} onChange={(e) => set("event_type", e.target.value)}>
            {Object.entries(EVENT_TYPES).map(([k, v]) => (
              <option key={k} value={k}>{v.label}</option>
            ))}
          </NativeSelect>
        </Field>

        <div className="flex items-end gap-2 pb-1.5">
          <label className="flex items-center gap-2 text-sm">
            <Switch checked={form.all_day} onCheckedChange={(v) => set("all_day", v)} />
            تمام‌روز
          </label>
        </div>

        <Field label="تاریخ شروع" htmlFor="ev-date">
          <div className="flex gap-1.5">
            <JalaliDateInput id="ev-date" value={form.date} onChange={(d) => set("date", d)} className="flex-1" />
            {!form.all_day && (
              <Input
                dir="ltr" type="time" value={form.time} onChange={(e) => set("time", e.target.value)}
                className="w-24 tabular" aria-label="ساعت شروع"
              />
            )}
          </div>
        </Field>

        <Field label="تاریخ پایان (اختیاری)" htmlFor="ev-end-date">
          <div className="flex gap-1.5">
            <JalaliDateInput id="ev-end-date" value={form.endDate} onChange={(d) => set("endDate", d)} className="flex-1" />
            {!form.all_day && (
              <Input
                dir="ltr" type="time" value={form.endTime} onChange={(e) => set("endTime", e.target.value)}
                className="w-24 tabular" aria-label="ساعت پایان"
              />
            )}
          </div>
        </Field>

        <Field
          label="کد ملک (اختیاری)"
          htmlFor="ev-serial"
          hint={
            !form.property_serial.trim()
              ? undefined
              : lookingUp
                ? "در حال جستجو…"
                : lookup === "notfound"
                  ? "ملکی با این کد یافت نشد"
                  : lookup && typeof lookup === "object"
                    ? lookup.title
                    : undefined
          }
        >
          <Input
            id="ev-serial" dir="ltr" inputMode="numeric" value={form.property_serial}
            onChange={(e) => set("property_serial", e.target.value.replace(/\D/g, ""))} className="tabular"
          />
        </Field>
        <Field label="محل بازدید" htmlFor="ev-location">
          <Input id="ev-location" value={form.location} onChange={(e) => set("location", e.target.value)} />
        </Field>

        <Field label="نام مالک" htmlFor="ev-owner-name">
          <Input id="ev-owner-name" value={form.owner_name} onChange={(e) => set("owner_name", e.target.value)} />
        </Field>
        <Field label="شمارهٔ مالک" htmlFor="ev-owner-phone">
          <Input id="ev-owner-phone" dir="ltr" value={form.owner_phone} onChange={(e) => set("owner_phone", e.target.value)} className="tabular" />
        </Field>

        <Field label="نام مشتری" htmlFor="ev-customer-name">
          <Input id="ev-customer-name" value={form.customer_name} onChange={(e) => set("customer_name", e.target.value)} />
        </Field>
        <Field label="شمارهٔ مشتری" htmlFor="ev-customer-phone">
          <Input id="ev-customer-phone" dir="ltr" value={form.customer_phone} onChange={(e) => set("customer_phone", e.target.value)} className="tabular" />
        </Field>

        <Field label="کارشناس فروش" htmlFor="ev-assigned">
          <Input id="ev-assigned" value={form.assigned_to} onChange={(e) => set("assigned_to", e.target.value)} />
        </Field>
        <Field label="شمارهٔ کارشناس" htmlFor="ev-agent-phone">
          <Input id="ev-agent-phone" dir="ltr" value={form.agent_phone} onChange={(e) => set("agent_phone", e.target.value)} className="tabular" />
        </Field>

        <Field label="یادآوری" htmlFor="ev-remind">
          <NativeSelect id="ev-remind" value={form.remind_before} onChange={(e) => set("remind_before", Number(e.target.value))}>
            {REMIND_OPTIONS.map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
          </NativeSelect>
        </Field>
        {editing && (
          <Field label="وضعیت" htmlFor="ev-status">
            <NativeSelect id="ev-status" value={form.status} onChange={(e) => set("status", e.target.value)}>
              <option value="scheduled">برنامه‌ریزی‌شده</option>
              <option value="done">انجام شد</option>
              <option value="canceled">لغو شد</option>
            </NativeSelect>
          </Field>
        )}

        <div className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2 sm:col-span-2">
          <label htmlFor="ev-sms" className="flex flex-col text-sm">
            <span className="font-medium">یادآوری پیامکی</span>
            {smsHint && <span className="mt-0.5 text-xs text-muted-foreground">{smsHint}</span>}
          </label>
          <Switch id="ev-sms" checked={form.sms_reminder} onCheckedChange={(v) => set("sms_reminder", v)} />
        </div>

        <Field label="توضیحات" htmlFor="ev-desc" className="sm:col-span-2">
          <Textarea id="ev-desc" value={form.description} onChange={(e) => set("description", e.target.value)} rows={2} />
        </Field>
        <Field label="نتیجه (پس از انجام قرار)" htmlFor="ev-outcome" className="sm:col-span-2">
          <Textarea id="ev-outcome" value={form.outcome} onChange={(e) => set("outcome", e.target.value)} rows={2} />
        </Field>

        <details className="sm:col-span-2 rounded-lg border px-3 py-2 text-sm">
          <summary className="cursor-pointer select-none font-medium text-muted-foreground">پیوندها (اختیاری)</summary>
          <div className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Field label="شناسهٔ لید" htmlFor="ev-lead-id">
              <Input id="ev-lead-id" dir="ltr" inputMode="numeric" value={form.lead_id} onChange={(e) => set("lead_id", e.target.value.replace(/\D/g, ""))} className="tabular" />
            </Field>
            <Field label="شناسهٔ مشتری" htmlFor="ev-customer-id">
              <Input id="ev-customer-id" dir="ltr" inputMode="numeric" value={form.customer_id} onChange={(e) => set("customer_id", e.target.value.replace(/\D/g, ""))} className="tabular" />
            </Field>
            <Field label="شناسهٔ مخاطب" htmlFor="ev-contact-id">
              <Input id="ev-contact-id" dir="ltr" inputMode="numeric" value={form.contact_id} onChange={(e) => set("contact_id", e.target.value.replace(/\D/g, ""))} className="tabular" />
            </Field>
            <Field label="شناسهٔ معامله" htmlFor="ev-deal-id">
              <Input id="ev-deal-id" dir="ltr" inputMode="numeric" value={form.deal_id} onChange={(e) => set("deal_id", e.target.value.replace(/\D/g, ""))} className="tabular" />
            </Field>
          </div>
        </details>

        {form.date && (
          <p className="text-xs text-muted-foreground sm:col-span-2">
            {formatJalali(form.date, !form.all_day)}
          </p>
        )}
      </div>
    </RingDialog>
  );
}
