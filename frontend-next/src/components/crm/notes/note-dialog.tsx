"use client";

// New/edit sticky note (PUT /crm/notes/{id} — the old panel only had create
// and delete; edit is added here). A note can link to a contact (picked by
// search), a deal or a property (typed by id, the way the old panel did).

import { NotebookPen } from "lucide-react";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Field, RingDialog } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { ContactPicker, type PickedContact } from "../contacts/contact-picker";

export type NoteRecord = {
  id: number;
  content: string;
  contact_id: number | null;
  property_id: number | null;
  deal_id: number | null;
  created_by: string | null;
  created_at: string | null;
};

type FormState = { content: string; contact: PickedContact | null; property_id: string; deal_id: string };

function fromRecord(n: NoteRecord): FormState {
  return {
    content: n.content ?? "",
    contact: n.contact_id ? { id: n.contact_id, name: `مخاطب #${n.contact_id}` } : null,
    property_id: n.property_id ? String(n.property_id) : "",
    deal_id: n.deal_id ? String(n.deal_id) : "",
  };
}

const EMPTY: FormState = { content: "", contact: null, property_id: "", deal_id: "" };

export function NoteDialog({
  open, onOpenChange, note, onSaved,
}: { open: boolean; onOpenChange: (o: boolean) => void; note: NoteRecord | null; onSaved: () => void }) {
  return (
    <RingDialog open={open} onOpenChange={onOpenChange} icon={NotebookPen} title={note ? "ویرایش یادداشت" : "یادداشت جدید"}>
      {open && (
        <NoteForm key={note?.id ?? "new"} initial={note ? fromRecord(note) : EMPTY} noteId={note?.id ?? null} onSaved={onSaved} onClose={() => onOpenChange(false)} />
      )}
    </RingDialog>
  );
}

function NoteForm({
  initial, noteId, onSaved, onClose,
}: { initial: FormState; noteId: number | null; onSaved: () => void; onClose: () => void }) {
  const [form, setForm] = useState<FormState>(initial);
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setForm((f) => ({ ...f, [k]: v }));

  const submit = useMutation({
    mutationFn: () => {
      if (noteId) return api(`/crm/notes/${noteId}`, { method: "PUT", json: { content: form.content.trim() } });
      const dealId = form.deal_id.trim() ? Number(form.deal_id.trim()) : null;
      const propertyId = form.property_id.trim() ? Number(form.property_id.trim()) : null;
      return api("/crm/notes", {
        json: {
          content: form.content.trim(),
          contact_id: form.contact?.id ?? null,
          deal_id: Number.isFinite(dealId) ? dealId : null,
          property_id: Number.isFinite(propertyId) ? propertyId : null,
        },
      });
    },
    onSuccess: () => {
      toast.success(noteId ? "یادداشت به‌روزرسانی شد" : "یادداشت ثبت شد");
      onSaved();
      onClose();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "ذخیره ناموفق بود"),
  });

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.content.trim()) {
      toast.error("متن یادداشت الزامی است");
      return;
    }
    submit.mutate();
  }

  return (
    <form onSubmit={onSubmit} className="grid gap-3">
      <Field label="متن یادداشت" htmlFor="nt-content">
        <Textarea id="nt-content" rows={4} value={form.content} onChange={(e) => set("content", e.target.value)} required autoFocus />
      </Field>
      {!noteId && (
        <>
          <Field label="مخاطب (اختیاری)" htmlFor="nt-contact">
            <ContactPicker id="nt-contact" value={form.contact} onChange={(c) => set("contact", c)} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="کد معامله" htmlFor="nt-deal">
              <Input id="nt-deal" dir="ltr" inputMode="numeric" className="text-end" value={form.deal_id} onChange={(e) => set("deal_id", e.target.value)} />
            </Field>
            <Field label="کد ملک" htmlFor="nt-property">
              <Input id="nt-property" dir="ltr" inputMode="numeric" className="text-end" value={form.property_id} onChange={(e) => set("property_id", e.target.value)} />
            </Field>
          </div>
        </>
      )}
      <div className="grid gap-2">
        <Button type="submit" className="w-full" disabled={submit.isPending}>{submit.isPending ? "در حال ذخیره…" : "ذخیرهٔ یادداشت"}</Button>
        <Button type="button" variant="ghost" className="w-full" onClick={onClose}>انصراف</Button>
      </div>
    </form>
  );
}
