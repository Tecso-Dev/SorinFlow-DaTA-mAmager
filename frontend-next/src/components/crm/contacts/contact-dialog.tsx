"use client";

// New/edit phone-book contact.

import { BookUser } from "lucide-react";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Field, ListSkeleton, NativeSelect, RingDialog } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { CONTACT_CATEGORY, CONTACT_TYPE, CONTACT_TYPES } from "@/lib/crm";
import { parseDigits } from "@/lib/format";
import { CityField } from "../customers/form-fields";

export type ContactRecord = {
  id: number;
  name: string;
  phone: string | null;
  phone2: string | null;
  email: string | null;
  contact_type: string | null;
  category: string | null;
  city: string | null;
  address: string | null;
  notes: string | null;
  tags: string | null;
};

type FormState = {
  name: string; phone: string; phone2: string; email: string;
  contact_type: string; category: string; city: string; address: string; notes: string; tags: string;
};

const EMPTY: FormState = {
  name: "", phone: "", phone2: "", email: "", contact_type: "owner",
  category: "normal", city: "", address: "", notes: "", tags: "",
};

function fromRecord(c: ContactRecord): FormState {
  return {
    name: c.name ?? "", phone: c.phone ?? "", phone2: c.phone2 ?? "", email: c.email ?? "",
    contact_type: c.contact_type ?? "owner", category: c.category ?? "normal", city: c.city ?? "",
    address: c.address ?? "", notes: c.notes ?? "", tags: c.tags ?? "",
  };
}

function ContactForm({
  initial, contactId, onSaved, onClose,
}: { initial: FormState; contactId: number | null; onSaved: () => void; onClose: () => void }) {
  const [form, setForm] = useState<FormState>(initial);
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setForm((f) => ({ ...f, [k]: v }));

  const submit = useMutation({
    mutationFn: () => {
      const payload = {
        name: form.name.trim(),
        phone: parseDigits(form.phone).trim() || null,
        phone2: parseDigits(form.phone2).trim() || null,
        email: form.email.trim() || null,
        contact_type: form.contact_type,
        category: form.category,
        city: form.city.trim() || null,
        address: form.address.trim() || null,
        notes: form.notes.trim() || null,
        tags: form.tags,
      };
      return contactId
        ? api(`/crm/contacts/${contactId}`, { method: "PUT", json: payload })
        : api("/crm/contacts", { method: "POST", json: payload });
    },
    onSuccess: () => {
      toast.success(contactId ? "مخاطب به‌روزرسانی شد" : "مخاطب ثبت شد");
      onSaved();
      onClose();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "ذخیره ناموفق بود"),
  });

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.name.trim()) {
      toast.error("نام مخاطب الزامی است");
      return;
    }
    submit.mutate();
  }

  return (
    <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2">
      <Field label="نام" htmlFor="ct-name">
        <Input id="ct-name" value={form.name} onChange={(e) => set("name", e.target.value)} required />
      </Field>
      <Field label="دسته" htmlFor="ct-type">
        <NativeSelect id="ct-type" value={form.contact_type} onChange={(e) => set("contact_type", e.target.value)}>
          {CONTACT_TYPES.map((v) => <option key={v} value={v}>{CONTACT_TYPE[v]?.label ?? v}</option>)}
        </NativeSelect>
      </Field>
      <Field label="تلفن ۱" htmlFor="ct-phone">
        <Input id="ct-phone" dir="ltr" inputMode="tel" value={form.phone} onChange={(e) => set("phone", e.target.value)} />
      </Field>
      <Field label="تلفن ۲" htmlFor="ct-phone2">
        <Input id="ct-phone2" dir="ltr" inputMode="tel" value={form.phone2} onChange={(e) => set("phone2", e.target.value)} />
      </Field>
      <Field label="ایمیل" htmlFor="ct-email">
        <Input id="ct-email" dir="ltr" type="email" value={form.email} onChange={(e) => set("email", e.target.value)} />
      </Field>
      <Field label="اولویت" htmlFor="ct-category">
        <NativeSelect id="ct-category" value={form.category} onChange={(e) => set("category", e.target.value)}>
          {Object.entries(CONTACT_CATEGORY).map(([v, l]) => <option key={v} value={v}>{l.label}</option>)}
        </NativeSelect>
      </Field>
      <Field label="شهر" htmlFor="ct-city">
        <CityField id="ct-city" value={form.city} onChange={(v) => set("city", v)} />
      </Field>
      <Field label="تگ‌ها" htmlFor="ct-tags" hint="با کاما جدا کنید">
        <Input id="ct-tags" value={form.tags} onChange={(e) => set("tags", e.target.value)} placeholder="ویژه، پیگیری فوری" />
      </Field>
      <Field label="آدرس" htmlFor="ct-address" className="sm:col-span-2">
        <Textarea id="ct-address" rows={2} value={form.address} onChange={(e) => set("address", e.target.value)} />
      </Field>
      <Field label="یادداشت" htmlFor="ct-notes" className="sm:col-span-2">
        <Textarea id="ct-notes" rows={2} value={form.notes} onChange={(e) => set("notes", e.target.value)} />
      </Field>
      <div className="grid gap-2 sm:col-span-2 sm:grid-cols-2">
        <Button type="submit" disabled={submit.isPending}>{submit.isPending ? "در حال ذخیره…" : "ذخیرهٔ مخاطب"}</Button>
        <Button type="button" variant="ghost" onClick={onClose}>انصراف</Button>
      </div>
    </form>
  );
}

export function ContactDialog({
  open, onOpenChange, contactId, onSaved,
}: { open: boolean; onOpenChange: (o: boolean) => void; contactId: number | null; onSaved: () => void }) {
  const query = useQuery({
    queryKey: ["crm", "contact", contactId],
    queryFn: () => api<ContactRecord>(`/crm/contacts/${contactId}`),
    enabled: open && contactId !== null,
  });

  return (
    <RingDialog open={open} onOpenChange={onOpenChange} icon={BookUser} title={contactId ? "ویرایش مخاطب" : "مخاطب جدید"} wide>
      {!open ? null : contactId && query.isLoading ? (
        <ListSkeleton rows={4} />
      ) : contactId && query.isError ? (
        <p className="text-sm text-destructive">بارگیری مخاطب ناموفق بود.</p>
      ) : (
        <ContactForm
          key={contactId ?? "new"}
          initial={query.data ? fromRecord(query.data) : EMPTY}
          contactId={contactId}
          onSaved={onSaved}
          onClose={() => onOpenChange(false)}
        />
      )}
    </RingDialog>
  );
}
