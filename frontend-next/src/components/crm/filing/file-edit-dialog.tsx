"use client";

// GET/PATCH /filing/files/{id} — everything a consultant corrects by hand
// after a scrape or a phone call (app/api/routes/filing.py FILE_TEXT/FILE_INT/FILE_BOOL).

import { PencilLine } from "lucide-react";
import { useEffect, useState } from "react";
import { Field, NativeSelect, RingDialog } from "@/components/panel/kit";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { CORNER_OPTIONS, DIRECTION_OPTIONS } from "@/lib/crm";
import { allBinders, type Cabinet, type FileFull } from "./types";

type Form = Record<string, string | boolean>;

const TEXT_FIELDS: [keyof FileFull, string][] = [
  ["title", "عنوان"], ["property_type", "نوع ملک"], ["district", "منطقه"], ["neighborhood", "محله"],
  ["address", "آدرس"], ["seller_name", "نام فروشنده/مالک"], ["phone_number", "شماره تماس"],
  ["document_type", "نوع سند"], ["unit_status", "وضعیت واحد"],
];
const NUM_FIELDS: [keyof FileFull, string][] = [
  ["area", "متراژ"], ["rooms", "تعداد اتاق"], ["floor", "طبقه"], ["total_floors", "کل طبقات"],
  ["year_built", "سال ساخت"], ["total_price", "قیمت کل"], ["price_per_meter", "قیمت هر متر"],
  ["deposit", "ودیعه/رهن"], ["rent_price", "اجارهٔ ماهانه"],
];
const BOOL_FIELDS: [keyof FileFull, string][] = [
  ["has_elevator", "آسانسور"], ["has_parking", "پارکینگ"], ["has_storage", "انباری"], ["has_balcony", "بالکن"],
];

function toForm(f: FileFull): Form {
  const out: Form = {};
  for (const [k] of TEXT_FIELDS) out[k] = (f[k] as string) ?? "";
  for (const [k] of NUM_FIELDS) out[k] = f[k] != null ? String(f[k]) : "";
  for (const [k] of BOOL_FIELDS) out[k] = !!f[k];
  out.building_direction = f.building_direction ?? "";
  out.corner_type = f.corner_type ?? "";
  out.description = f.description ?? "";
  out.tags = f.tags.join("، ");
  out.is_pinned = !!f.is_pinned;
  out.is_private = !!f.is_private;
  out.is_draft = !!f.is_draft;
  out.binder_id = f.binder_id ? String(f.binder_id) : "";
  return out;
}

export function FileEditDialog({
  open, onOpenChange, fileId, cabinets, onSaved,
}: { open: boolean; onOpenChange: (o: boolean) => void; fileId: number | null; cabinets: Cabinet[]; onSaved: () => void }) {
  const [form, setForm] = useState<Form | null>(null);
  const [saving, setSaving] = useState(false);
  const set = (k: string, v: string | boolean) => setForm((f) => (f ? { ...f, [k]: v } : f));

  // the caller remounts this dialog (a fresh `key`) on every open, so `form`
  // is already back to its initial null — nothing to reset here
  useEffect(() => {
    if (!open || !fileId) return;
    api<FileFull>(`/filing/files/${fileId}`)
      .then((f) => setForm(toForm(f)))
      .catch((e) => toast.error("فایل یافت نشد", e instanceof ApiError ? e.message : undefined));
  }, [open, fileId]);

  async function save() {
    if (!form || !fileId) return;
    if (!(form.title as string).trim()) {
      toast.error("عنوان فایل خالی نمی‌تواند باشد");
      return;
    }
    setSaving(true);
    const body: Record<string, unknown> = {};
    for (const [k] of TEXT_FIELDS) body[k] = (form[k] as string).trim() || null;
    for (const [k] of NUM_FIELDS) body[k] = (form[k] as string).trim() || null;
    for (const [k] of BOOL_FIELDS) body[k] = form[k];
    body.building_direction = form.building_direction || null;
    body.corner_type = form.corner_type || null;
    body.description = (form.description as string).trim() || null;
    body.tags = (form.tags as string).trim() || null;
    body.is_pinned = form.is_pinned;
    body.is_private = form.is_private;
    body.is_draft = form.is_draft;
    body.binder_id = form.binder_id ? Number(form.binder_id) : null;
    try {
      await api(`/filing/files/${fileId}`, { method: "PATCH", json: body });
      toast.success("فایل به‌روزرسانی شد");
      onOpenChange(false);
      onSaved();
    } catch (e) {
      toast.error("ذخیره نشد", e instanceof ApiError ? e.message : undefined);
    } finally {
      setSaving(false);
    }
  }

  const binders = allBinders(cabinets);

  return (
    <RingDialog
      open={open}
      onOpenChange={onOpenChange}
      icon={PencilLine}
      wide
      title="ویرایش فایل"
      footer={<Button className="w-full" disabled={saving || !form} onClick={save}>ذخیرهٔ تغییرات</Button>}
    >
      {!form ? (
        <div className="grid gap-2">
          {Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-8 w-full" />)}
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {TEXT_FIELDS.map(([k, label]) => (
            <Field key={k} label={label} htmlFor={`fe-${k}`} className={k === "title" || k === "address" ? "sm:col-span-2" : undefined}>
              <Input id={`fe-${k}`} value={form[k] as string} onChange={(e) => set(k, e.target.value)} dir={k === "phone_number" ? "ltr" : undefined} className={k === "phone_number" ? "tabular" : undefined} />
            </Field>
          ))}
          <Field label="زونکن" htmlFor="fe-binder">
            <NativeSelect id="fe-binder" value={form.binder_id as string} onChange={(e) => set("binder_id", e.target.value)}>
              <option value="">بدون زونکن</option>
              {binders.map((b) => <option key={b.id} value={b.id}>{b.parent_id ? `↳ ${b.name}` : b.name}</option>)}
            </NativeSelect>
          </Field>
          <Field label="جهت ساختمان" htmlFor="fe-direction">
            <NativeSelect id="fe-direction" value={form.building_direction as string} onChange={(e) => set("building_direction", e.target.value)}>
              <option value="">—</option>
              {DIRECTION_OPTIONS.map((d) => <option key={d} value={d}>{d}</option>)}
            </NativeSelect>
          </Field>
          <Field label="نبش" htmlFor="fe-corner">
            <NativeSelect id="fe-corner" value={form.corner_type as string} onChange={(e) => set("corner_type", e.target.value)}>
              <option value="">—</option>
              {CORNER_OPTIONS.map((d) => <option key={d} value={d}>{d}</option>)}
            </NativeSelect>
          </Field>

          {NUM_FIELDS.map(([k, label]) => (
            <Field key={k} label={label} htmlFor={`fe-${k}`}>
              <Input id={`fe-${k}`} dir="ltr" inputMode="numeric" className="tabular" value={form[k] as string} onChange={(e) => set(k, e.target.value.replace(/[^\d/]/g, ""))} />
            </Field>
          ))}

          <div className="flex flex-wrap gap-3 text-sm sm:col-span-2">
            {BOOL_FIELDS.map(([k, label]) => (
              <label key={k} className="flex items-center gap-1.5">
                <Checkbox checked={form[k] as boolean} onCheckedChange={(v) => set(k, v === true)} /> {label}
              </label>
            ))}
          </div>

          <Field label="توضیحات" htmlFor="fe-desc" className="sm:col-span-2">
            <Textarea id="fe-desc" value={form.description as string} onChange={(e) => set("description", e.target.value)} rows={3} />
          </Field>
          <Field label="برچسب‌ها (با ، جدا کنید)" htmlFor="fe-tags" className="sm:col-span-2">
            <Input id="fe-tags" value={form.tags as string} onChange={(e) => set("tags", e.target.value)} />
          </Field>

          <div className="flex flex-wrap gap-4 text-sm sm:col-span-2">
            <label className="flex items-center gap-1.5">
              <Checkbox checked={form.is_pinned as boolean} onCheckedChange={(v) => set("is_pinned", v === true)} /> سنجاق‌شده
            </label>
            <label className="flex items-center gap-1.5">
              <Checkbox checked={form.is_private as boolean} onCheckedChange={(v) => set("is_private", v === true)} /> فایل شخصی
            </label>
            <label className="flex items-center gap-1.5">
              <Checkbox checked={form.is_draft as boolean} onCheckedChange={(v) => set("is_draft", v === true)} /> پیش‌نویس
            </label>
          </div>
        </div>
      )}
    </RingDialog>
  );
}
