"use client";

// New/edit deal (معامله): a title, buy/rent/lease, a contact picker for the
// buyer and seller instead of typing their IDs, amount and commission in
// toman, and the contract/close dates.

import { Handshake } from "lucide-react";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Field, ListSkeleton, NativeSelect, RingDialog } from "@/components/panel/kit";
import { JalaliDateInput } from "@/components/panel/date-input";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { DEAL_STATUS, DEAL_TYPE } from "@/lib/crm";
import { MoneyInput } from "../customers/form-fields";
import { ContactPicker, type PickedContact } from "../contacts/contact-picker";

export type DealRecord = {
  id: number;
  title: string;
  deal_type: string | null;
  status: string | null;
  property_id: number | null;
  buyer_contact_id: number | null;
  seller_contact_id: number | null;
  buyer_name: string | null;
  seller_name: string | null;
  amount: number | null;
  commission: number | null;
  commission_paid: boolean;
  notes: string | null;
  contract_date: string | null;
  close_date: string | null;
};

type FormState = {
  title: string;
  deal_type: string;
  status: string;
  property_id: string;
  buyer: PickedContact | null;
  seller: PickedContact | null;
  amount: number | null;
  commission: number | null;
  commission_paid: boolean;
  notes: string;
  contract_date: Date | null;
  close_date: Date | null;
};

const EMPTY: FormState = {
  title: "", deal_type: "buy", status: "new", property_id: "", buyer: null, seller: null,
  amount: null, commission: null, commission_paid: false, notes: "", contract_date: null, close_date: null,
};

function fromRecord(d: DealRecord): FormState {
  return {
    title: d.title ?? "", deal_type: d.deal_type ?? "buy", status: d.status ?? "new",
    property_id: d.property_id ? String(d.property_id) : "",
    buyer: d.buyer_contact_id ? { id: d.buyer_contact_id, name: d.buyer_name ?? `#${d.buyer_contact_id}` } : null,
    seller: d.seller_contact_id ? { id: d.seller_contact_id, name: d.seller_name ?? `#${d.seller_contact_id}` } : null,
    amount: d.amount ?? null, commission: d.commission ?? null, commission_paid: !!d.commission_paid,
    notes: d.notes ?? "",
    contract_date: d.contract_date ? new Date(d.contract_date) : null,
    close_date: d.close_date ? new Date(d.close_date) : null,
  };
}

function DealForm({
  initial, dealId, onSaved, onClose,
}: { initial: FormState; dealId: number | null; onSaved: () => void; onClose: () => void }) {
  const [form, setForm] = useState<FormState>(initial);
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setForm((f) => ({ ...f, [k]: v }));

  const submit = useMutation({
    mutationFn: () => {
      const propertyId = form.property_id.trim() ? Number(form.property_id.trim()) : null;
      const payload = {
        title: form.title.trim(),
        deal_type: form.deal_type,
        status: form.status,
        property_id: Number.isFinite(propertyId) ? propertyId : null,
        buyer_contact_id: form.buyer?.id ?? null,
        seller_contact_id: form.seller?.id ?? null,
        amount: form.amount,
        commission: form.commission,
        commission_paid: form.commission_paid,
        notes: form.notes.trim() || null,
        contract_date: form.contract_date ? form.contract_date.toISOString() : null,
        close_date: form.close_date ? form.close_date.toISOString() : null,
      };
      return dealId
        ? api(`/crm/deals/${dealId}`, { method: "PUT", json: payload })
        : api("/crm/deals", { method: "POST", json: payload });
    },
    onSuccess: () => {
      toast.success(dealId ? "معامله به‌روزرسانی شد" : "معامله ثبت شد");
      onSaved();
      onClose();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "ذخیره ناموفق بود"),
  });

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.title.trim()) {
      toast.error("عنوان معامله الزامی است");
      return;
    }
    submit.mutate();
  }

  return (
    <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2">
      <Field label="عنوان" htmlFor="dl-title" className="sm:col-span-2">
        <Input id="dl-title" value={form.title} onChange={(e) => set("title", e.target.value)} required />
      </Field>
      <Field label="نوع معامله" htmlFor="dl-type">
        <NativeSelect id="dl-type" value={form.deal_type} onChange={(e) => set("deal_type", e.target.value)}>
          <option value="buy">{DEAL_TYPE.buy}</option>
          <option value="rent">{DEAL_TYPE.rent}</option>
          <option value="lease">{DEAL_TYPE.lease}</option>
        </NativeSelect>
      </Field>
      <Field label="وضعیت" htmlFor="dl-status">
        <NativeSelect id="dl-status" value={form.status} onChange={(e) => set("status", e.target.value)}>
          {Object.entries(DEAL_STATUS).map(([v, l]) => <option key={v} value={v}>{l.label}</option>)}
        </NativeSelect>
      </Field>
      <Field label="خریدار" htmlFor="dl-buyer">
        <ContactPicker id="dl-buyer" value={form.buyer} onChange={(c) => set("buyer", c)} placeholder="جستجوی خریدار…" />
      </Field>
      <Field label="فروشنده" htmlFor="dl-seller">
        <ContactPicker id="dl-seller" value={form.seller} onChange={(c) => set("seller", c)} placeholder="جستجوی فروشنده…" />
      </Field>
      <Field label="مبلغ (تومان)" htmlFor="dl-amount">
        <MoneyInput id="dl-amount" value={form.amount} onChange={(n) => set("amount", n)} />
      </Field>
      <Field label="کمیسیون (تومان)" htmlFor="dl-commission">
        <MoneyInput id="dl-commission" value={form.commission} onChange={(n) => set("commission", n)} />
      </Field>
      <Field label="کد ملک (اختیاری)" htmlFor="dl-property" hint="شناسهٔ عددی ملک">
        <Input id="dl-property" dir="ltr" inputMode="numeric" className="text-end" value={form.property_id} onChange={(e) => set("property_id", e.target.value)} />
      </Field>
      <div className="flex items-end pb-1.5">
        <label className="group/field-label flex items-center gap-2 text-sm">
          <Checkbox checked={form.commission_paid} onCheckedChange={(c) => set("commission_paid", !!c)} />
          کمیسیون پرداخت شده
        </label>
      </div>
      <Field label="تاریخ قرارداد" htmlFor="dl-contract">
        <JalaliDateInput id="dl-contract" value={form.contract_date} onChange={(d) => set("contract_date", d)} />
      </Field>
      <Field label="تاریخ نهایی‌سازی" htmlFor="dl-close">
        <JalaliDateInput id="dl-close" value={form.close_date} onChange={(d) => set("close_date", d)} />
      </Field>
      <Field label="یادداشت" htmlFor="dl-notes" className="sm:col-span-2">
        <Textarea id="dl-notes" rows={2} value={form.notes} onChange={(e) => set("notes", e.target.value)} />
      </Field>
      <div className="grid gap-2 sm:col-span-2 sm:grid-cols-2">
        <Button type="submit" disabled={submit.isPending}>{submit.isPending ? "در حال ذخیره…" : "ذخیرهٔ معامله"}</Button>
        <Button type="button" variant="ghost" onClick={onClose}>انصراف</Button>
      </div>
    </form>
  );
}

export function DealDialog({
  open, onOpenChange, dealId, onSaved,
}: { open: boolean; onOpenChange: (o: boolean) => void; dealId: number | null; onSaved: () => void }) {
  const query = useQuery({
    queryKey: ["crm", "deal", dealId],
    queryFn: () => api<DealRecord>(`/crm/deals/${dealId}`),
    enabled: open && dealId !== null,
  });

  return (
    <RingDialog open={open} onOpenChange={onOpenChange} icon={Handshake} title={dealId ? "ویرایش معامله" : "معاملهٔ جدید"} wide>
      {!open ? null : dealId && query.isLoading ? (
        <ListSkeleton rows={4} />
      ) : dealId && query.isError ? (
        <p className="text-sm text-destructive">بارگیری معامله ناموفق بود.</p>
      ) : (
        <DealForm
          key={dealId ?? "new"}
          initial={query.data ? fromRecord(query.data) : EMPTY}
          dealId={dealId}
          onSaved={onSaved}
          onClose={() => onOpenChange(false)}
        />
      )}
    </RingDialog>
  );
}
