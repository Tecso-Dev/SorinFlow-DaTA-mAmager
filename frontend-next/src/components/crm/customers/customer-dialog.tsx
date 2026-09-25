"use client";

// New/edit customer (فرم پروفایل مشتری): lead profile, BANT, showing
// pipeline and follow-up loop, plus an AI reader that fills the free-text
// answer into these same columns (POST /api/ai/need/parse) — it only ever
// fills a field that is still empty, exactly like the old panel's version.

import { Plus, Sparkles, UserRound, Wand2, X } from "lucide-react";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { Field, ListSkeleton, NativeSelect, RingDialog } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { CUSTOMER_SOURCE, DEAL_TYPE, PROPERTY_KIND, SHOWING_STEP, TEMPERATURE } from "@/lib/crm";
import { faNum, parseDigits } from "@/lib/format";
import { CityField, MoneyInput } from "./form-fields";

export type Showing = { file_code: string; description: string; feedback: string; next_step: string };
export type Followup = { date: string; time: string; action: string };

export type CustomerRecord = {
  id: number;
  full_name: string;
  mobile1: string | null;
  mobile2: string | null;
  source: string | null;
  temperature: string | null;
  consultant_name: string | null;
  budget_max: number | null;
  payment_methods: string | null;
  desired_specs: string | null;
  desired_district: string | null;
  desired_city: string | null;
  desired_type: string | null;
  deal_type: string | null;
  red_lines: string | null;
  showings: Showing[];
  followups: Followup[];
  notes: string | null;
};

const PAYMENT_METHODS: Record<string, string> = {
  cash: "نقد", loan: "وام", has_property: "معاوضه با ملک", barter: "معاوضه",
};

// need_parser.to_customer_fields → the form controls that hold them (below).
const AI_FIELDS = [
  "desired_city", "desired_district", "desired_type", "deal_type",
  "budget_max", "desired_specs", "red_lines", "notes", "temperature",
] as const;
type AiField = (typeof AI_FIELDS)[number];
const AI_DEFAULTS: Partial<Record<AiField, string>> = { deal_type: "buy", temperature: "warm" };

type FormState = {
  full_name: string;
  mobile1: string;
  mobile2: string;
  source: string;
  temperature: string;
  consultant_name: string;
  budget_max: number | null;
  payment_methods: string[];
  desired_specs: string;
  desired_district: string;
  desired_city: string;
  desired_type: string;
  deal_type: string;
  red_lines: string;
  notes: string;
  showings: Showing[];
  followups: Followup[];
};

const EMPTY: FormState = {
  full_name: "", mobile1: "", mobile2: "", source: "in_person", temperature: "warm",
  consultant_name: "", budget_max: null, payment_methods: [], desired_specs: "",
  desired_district: "", desired_city: "", desired_type: "", deal_type: "buy", red_lines: "",
  notes: "", showings: [], followups: [],
};

function fromRecord(c: CustomerRecord): FormState {
  return {
    full_name: c.full_name ?? "", mobile1: c.mobile1 ?? "", mobile2: c.mobile2 ?? "",
    source: c.source ?? "in_person", temperature: c.temperature ?? "warm",
    consultant_name: c.consultant_name ?? "", budget_max: c.budget_max ?? null,
    payment_methods: (c.payment_methods ?? "").split(",").map((s) => s.trim()).filter(Boolean),
    desired_specs: c.desired_specs ?? "", desired_district: c.desired_district ?? "",
    desired_city: c.desired_city ?? "", desired_type: c.desired_type ?? "", deal_type: c.deal_type ?? "buy",
    red_lines: c.red_lines ?? "", notes: c.notes ?? "",
    showings: c.showings?.length ? c.showings : [],
    followups: c.followups?.length ? c.followups : [],
  };
}

function CustomerForm({
  initial, isNew, customerId, onSaved, onClose,
}: { initial: FormState; isNew: boolean; customerId: number | null; onSaved: () => void; onClose: () => void }) {
  const [form, setForm] = useState<FormState>(initial);
  const [aiText, setAiText] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [aiFilled, setAiFilled] = useState<Set<AiField>>(new Set());
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setForm((f) => ({ ...f, [k]: v }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.full_name.trim()) {
      toast.error("نام و نام خانوادگی الزامی است");
      return;
    }
    const payload = {
      full_name: form.full_name.trim(),
      mobile1: parseDigits(form.mobile1).trim() || null,
      mobile2: parseDigits(form.mobile2).trim() || null,
      source: form.source,
      temperature: form.temperature,
      consultant_name: form.consultant_name.trim() || null,
      budget_max: form.budget_max,
      payment_methods: form.payment_methods.join(","),
      desired_specs: form.desired_specs.trim() || null,
      desired_district: form.desired_district.trim() || null,
      desired_city: form.desired_city.trim() || null,
      desired_type: form.desired_type || null,
      deal_type: form.deal_type,
      red_lines: form.red_lines.trim() || null,
      notes: form.notes.trim() || null,
      showings: form.showings,
      followups: form.followups,
    };
    submitMutation.mutate(payload);
  }

  const submitMutation = useMutation({
    mutationFn: (payload: unknown) =>
      customerId
        ? api(`/crm/customers/${customerId}`, { method: "PUT", json: payload })
        : api("/crm/customers", { method: "POST", json: payload }),
    onSuccess: () => {
      toast.success(customerId ? "مشتری به‌روزرسانی شد" : "مشتری ثبت شد");
      onSaved();
      onClose();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "ذخیره ناموفق بود"),
  });

  function isEmpty(key: AiField): boolean {
    const v = form[key];
    const s = typeof v === "number" ? String(v) : (v ?? "").toString().trim();
    if (s === "") return true;
    return isNew && AI_DEFAULTS[key] !== undefined && AI_DEFAULTS[key] === s;
  }

  function flash(key: AiField) {
    setAiFilled((s) => new Set(s).add(key));
    setTimeout(() => setAiFilled((s) => { const n = new Set(s); n.delete(key); return n; }), 2000);
  }

  async function fillFromText() {
    const text = aiText.trim();
    if (!text) {
      toast.info("اول حرف مشتری را بنویسید");
      return;
    }
    setAiBusy(true);
    try {
      const hint: Record<string, string> = {};
      if (!isEmpty("desired_city")) hint.city = form.desired_city;
      if (!isEmpty("deal_type")) hint.deal_type = form.deal_type;
      const out = await api<{ customer: Record<string, unknown>; criteria: { confidence?: Record<string, number> } }>(
        "/ai/need/parse",
        { json: { text, hint } },
      );
      let filled = 0;
      for (const key of AI_FIELDS) {
        const value = out.customer[key];
        if (value === null || value === undefined || value === "") continue;
        if (!isEmpty(key)) continue;
        set(key, value as never);
        flash(key);
        filled++;
      }
      const conf = Object.values(out.criteria?.confidence ?? {});
      const range = conf.length
        ? (() => {
            const pct = (n: number) => `${faNum(Math.round(n * 100))}٪`;
            const lo = Math.min(...conf), hi = Math.max(...conf);
            return lo === hi ? pct(lo) : `${pct(lo)} تا ${pct(hi)}`;
          })()
        : null;
      if (filled) {
        toast.success(`${faNum(filled)} فیلد پر شد`, range ? `اطمینان مدل ${range} — بررسی کنید و ذخیره بزنید.` : "بررسی کنید و ذخیره بزنید.");
      } else {
        toast.info("چیز تازه‌ای پیدا نشد", "فیلدها یا پر بودند یا متن چیزی نگفته.");
      }
    } catch (e) {
      toast.error("هوش مصنوعی", e instanceof ApiError ? e.message : "خواندن متن ناموفق بود");
    } finally {
      setAiBusy(false);
    }
  }

  const aiCls = (key: AiField) => cn(aiFilled.has(key) && "ring-2 ring-success/60 transition-shadow");

  return (
    <form onSubmit={submit} className="grid gap-5">
      {/* ── AI reader ── */}
      <div className="rounded-xl border border-dashed bg-muted/30 p-3">
        <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
          <Wand2 className="size-3.5" /> حرف مشتری را همین‌طور که گفته بنویسید — فیلدهای خالی از آن پر می‌شوند
        </p>
        <Textarea
          rows={2}
          value={aiText}
          onChange={(e) => setAiText(e.target.value)}
          placeholder="یه واحد ۱۰۰ متری نوساز طرف گلها تا ۵ میلیارد، طبقهٔ اول نباشه، پارکینگ حتماً…"
        />
        <Button type="button" variant="secondary" size="sm" className="mt-2 gap-1.5" disabled={aiBusy} onClick={fillFromText}>
          <Sparkles className="size-3.5" /> {aiBusy ? "در حال خواندن…" : "پر کردن از متن"}
        </Button>
      </div>

      {/* ── پروفایل ── */}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="نام و نام خانوادگی" htmlFor="cf-name">
          <Input id="cf-name" value={form.full_name} onChange={(e) => set("full_name", e.target.value)} required />
        </Field>
        <Field label="مشاور" htmlFor="cf-consultant">
          <Input id="cf-consultant" value={form.consultant_name} onChange={(e) => set("consultant_name", e.target.value)} />
        </Field>
        <Field label="موبایل ۱" htmlFor="cf-mobile1">
          <Input id="cf-mobile1" dir="ltr" inputMode="tel" value={form.mobile1} onChange={(e) => set("mobile1", e.target.value)} />
        </Field>
        <Field label="موبایل ۲" htmlFor="cf-mobile2">
          <Input id="cf-mobile2" dir="ltr" inputMode="tel" value={form.mobile2} onChange={(e) => set("mobile2", e.target.value)} />
        </Field>
        <Field label="منبع" htmlFor="cf-source">
          <NativeSelect id="cf-source" value={form.source} onChange={(e) => set("source", e.target.value)}>
            <option value="in_person">{CUSTOMER_SOURCE.in_person}</option>
            <option value="divar">{CUSTOMER_SOURCE.divar}</option>
            <option value="referral">{CUSTOMER_SOURCE.referral}</option>
          </NativeSelect>
        </Field>
        <Field label="حرارت" htmlFor="cf-temperature">
          <NativeSelect id="cf-temperature" className={aiCls("temperature")} value={form.temperature} onChange={(e) => set("temperature", e.target.value)}>
            {Object.entries(TEMPERATURE).map(([v, l]) => (
              <option key={v} value={v}>{l.label}</option>
            ))}
          </NativeSelect>
        </Field>
      </div>

      {/* ── نیاز و بودجه (BANT) ── */}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="نوع معامله" htmlFor="cf-deal-type">
          <NativeSelect id="cf-deal-type" className={aiCls("deal_type")} value={form.deal_type} onChange={(e) => set("deal_type", e.target.value)}>
            <option value="buy">{DEAL_TYPE.buy}</option>
            <option value="rent">{DEAL_TYPE.rent}</option>
          </NativeSelect>
        </Field>
        <Field label="نوع ملک" htmlFor="cf-desired-type">
          <NativeSelect id="cf-desired-type" className={aiCls("desired_type")} value={form.desired_type} onChange={(e) => set("desired_type", e.target.value)}>
            <option value="">هر نوع</option>
            {Object.entries(PROPERTY_KIND).map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
          </NativeSelect>
        </Field>
        <Field label="شهر مورد نظر" htmlFor="cf-city">
          <CityField id="cf-city" className={aiCls("desired_city")} value={form.desired_city} onChange={(v) => set("desired_city", v)} placeholder="شهر" />
        </Field>
        <Field label="سقف بودجه (تومان)" htmlFor="cf-budget">
          <MoneyInput id="cf-budget" className={aiCls("budget_max")} value={form.budget_max} onChange={(n) => set("budget_max", n)} />
        </Field>
        <Field label="منطقهٔ درخواستی" htmlFor="cf-district" className="sm:col-span-2">
          <Input id="cf-district" className={aiCls("desired_district")} value={form.desired_district} onChange={(e) => set("desired_district", e.target.value)} placeholder="مثلاً گلها، فلکه دوم" />
        </Field>
        <Field label="متراژ / خواب" htmlFor="cf-specs">
          <Input id="cf-specs" className={aiCls("desired_specs")} value={form.desired_specs} onChange={(e) => set("desired_specs", e.target.value)} placeholder="۱۰۰ متر / ۲ خواب" />
        </Field>
        <Field label="خط قرمزها" htmlFor="cf-redlines">
          <Input id="cf-redlines" className={aiCls("red_lines")} value={form.red_lines} onChange={(e) => set("red_lines", e.target.value)} placeholder="طبقهٔ اول نباشه" />
        </Field>
        <div className="sm:col-span-2">
          <p className="mb-1.5 text-sm font-medium">نحوهٔ پرداخت</p>
          <div className="flex flex-wrap gap-4">
            {Object.entries(PAYMENT_METHODS).map(([v, l]) => (
              <label key={v} className="group/field-label flex items-center gap-1.5 text-sm">
                <Checkbox
                  checked={form.payment_methods.includes(v)}
                  onCheckedChange={(c) =>
                    set("payment_methods", c ? [...form.payment_methods, v] : form.payment_methods.filter((x) => x !== v))
                  }
                />
                {l}
              </label>
            ))}
          </div>
        </div>
        <Field label="یادداشت" htmlFor="cf-notes" className="sm:col-span-2">
          <Textarea id="cf-notes" className={aiCls("notes")} rows={2} value={form.notes} onChange={(e) => set("notes", e.target.value)} />
        </Field>
      </div>

      {/* ── خط تولید بازدید ── */}
      <div>
        <div className="mb-2 flex items-center justify-between">
          <p className="text-sm font-bold">بازدیدها</p>
          <Button
            type="button" variant="outline" size="sm" className="gap-1"
            onClick={() => set("showings", [...form.showings, { file_code: "", description: "", feedback: "", next_step: "" }])}
          >
            <Plus className="size-3.5" /> ردیف تازه
          </Button>
        </div>
        <div className="grid gap-2">
          {form.showings.map((s, i) => (
            <div key={i} className="grid grid-cols-1 gap-2 rounded-lg border p-2 sm:grid-cols-[1fr_2fr_2fr_1fr_auto] sm:items-center">
              <Input placeholder="کد فایل" dir="ltr" className="text-end" value={s.file_code}
                onChange={(e) => set("showings", form.showings.map((r, j) => (j === i ? { ...r, file_code: e.target.value } : r)))} />
              <Input placeholder="شرح ملک" value={s.description}
                onChange={(e) => set("showings", form.showings.map((r, j) => (j === i ? { ...r, description: e.target.value } : r)))} />
              <Input placeholder="بازخورد" value={s.feedback}
                onChange={(e) => set("showings", form.showings.map((r, j) => (j === i ? { ...r, feedback: e.target.value } : r)))} />
              <NativeSelect value={s.next_step}
                onChange={(e) => set("showings", form.showings.map((r, j) => (j === i ? { ...r, next_step: e.target.value } : r)))}>
                <option value="">— گام بعدی —</option>
                {Object.entries(SHOWING_STEP).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </NativeSelect>
              <Button type="button" variant="ghost" size="icon-sm" aria-label="حذف ردیف"
                onClick={() => set("showings", form.showings.filter((_, j) => j !== i))}>
                <X className="size-4" />
              </Button>
            </div>
          ))}
          {form.showings.length === 0 && <p className="text-xs text-muted-foreground">بازدیدی ثبت نشده.</p>}
        </div>
      </div>

      {/* ── پیگیری بعدی ── */}
      <div>
        <div className="mb-2 flex items-center justify-between">
          <p className="text-sm font-bold">پیگیری‌ها</p>
          <Button
            type="button" variant="outline" size="sm" className="gap-1"
            onClick={() => set("followups", [...form.followups, { date: "", time: "", action: "" }])}
          >
            <Plus className="size-3.5" /> ردیف تازه
          </Button>
        </div>
        <div className="grid gap-2">
          {form.followups.map((f, i) => (
            <div key={i} className="grid grid-cols-1 gap-2 rounded-lg border p-2 sm:grid-cols-[1fr_1fr_3fr_auto] sm:items-center">
              <Input placeholder="۱۴۰۵/۰۵/۰۱" dir="ltr" className="text-end" value={f.date}
                onChange={(e) => set("followups", form.followups.map((r, j) => (j === i ? { ...r, date: e.target.value } : r)))} />
              <Input placeholder="۱۴:۳۰" dir="ltr" className="text-end" value={f.time}
                onChange={(e) => set("followups", form.followups.map((r, j) => (j === i ? { ...r, time: e.target.value } : r)))} />
              <Input placeholder="چه چیزی باید پیگیری یا ارائه شود؟" value={f.action}
                onChange={(e) => set("followups", form.followups.map((r, j) => (j === i ? { ...r, action: e.target.value } : r)))} />
              <Button type="button" variant="ghost" size="icon-sm" aria-label="حذف ردیف"
                onClick={() => set("followups", form.followups.filter((_, j) => j !== i))}>
                <X className="size-4" />
              </Button>
            </div>
          ))}
          {form.followups.length === 0 && <p className="text-xs text-muted-foreground">پیگیری‌ای ثبت نشده.</p>}
        </div>
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        <Button type="submit" disabled={submitMutation.isPending}>
          {submitMutation.isPending ? "در حال ذخیره…" : "ذخیرهٔ مشتری"}
        </Button>
        <Button type="button" variant="ghost" onClick={onClose}>انصراف</Button>
      </div>
    </form>
  );
}

export function CustomerDialog({
  open, onOpenChange, customerId, onSaved,
}: { open: boolean; onOpenChange: (o: boolean) => void; customerId: number | null; onSaved: () => void }) {
  const query = useQuery({
    queryKey: ["crm", "customer", customerId],
    queryFn: () => api<CustomerRecord>(`/crm/customers/${customerId}`),
    enabled: open && customerId !== null,
  });

  return (
    <RingDialog
      open={open}
      onOpenChange={onOpenChange}
      icon={UserRound}
      title={customerId ? "ویرایش مشتری" : "مشتری جدید"}
      wide
    >
      {!open ? null : customerId && query.isLoading ? (
        <ListSkeleton rows={5} />
      ) : customerId && query.isError ? (
        <p className="text-sm text-destructive">بارگیری مشتری ناموفق بود.</p>
      ) : (
        <CustomerForm
          key={customerId ?? "new"}
          initial={query.data ? fromRecord(query.data) : EMPTY}
          isNew={customerId === null}
          customerId={customerId}
          onSaved={onSaved}
          onClose={() => onOpenChange(false)}
        />
      )}
    </RingDialog>
  );
}
