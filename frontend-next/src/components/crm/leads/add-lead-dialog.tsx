"use client";

// «لید جدید»: a lead entered by hand. Photos upload first (POST
// /crm/upload-image hands back /images/manual/… urls), then the lead is
// created with them, the per-kind fields going in `attrs`.

import { ImagePlus, Loader2, Plus, X } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { useRef, useState } from "react";
import { Field, NativeSelect, RingDialog } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import { LEAD_STATUS, LEAD_STATUS_ORDER } from "@/lib/crm";
import { faNum, parseDigits } from "@/lib/format";
import { ADD_KINDS, LEAD_KIND_FIELDS, MoneyInput, toInt } from "./shared";

const MAX_PHOTOS = 20;

type Form = {
  property_title: string; property_kind: string; listing_type: string; category_name: string; city_name: string;
  price: number | null; deposit: number | null; rent_price: number | null; area: string; phone_number: string;
  seller_name: string; property_url: string; status: string; notes: string;
};
const EMPTY: Form = {
  property_title: "", property_kind: "", listing_type: "", category_name: "", city_name: "", price: null, deposit: null,
  rent_price: null, area: "", phone_number: "", seller_name: "", property_url: "", status: "new", notes: "",
};

export function AddLeadDialog({ open, onOpenChange, categories }: { open: boolean; onOpenChange: (o: boolean) => void; categories: { name: string; type: string }[] }) {
  const qc = useQueryClient();
  const [f, setF] = useState<Form>(EMPTY);
  const [attrs, setAttrs] = useState<Record<string, string>>({});
  const [photos, setPhotos] = useState<string[]>([]);
  const [uploading, setUploading] = useState<string | null>(null);
  const [tried, setTried] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((s) => ({ ...s, [k]: v }));
  const rent = f.listing_type === "rent";
  const fields = LEAD_KIND_FIELDS[f.property_kind] ?? [];

  function reset() {
    setF(EMPTY);
    setAttrs({});
    setPhotos([]);
    setTried(false);
  }

  async function upload(files: FileList | null) {
    const list = [...(files ?? [])];
    if (file.current) file.current.value = "";
    let room = MAX_PHOTOS - photos.length;
    for (const one of list) {
      if (room <= 0) {
        toast.info(`حداکثر ${faNum(MAX_PHOTOS)} تصویر`);
        break;
      }
      setUploading(one.name);
      try {
        const fd = new FormData();
        fd.append("file", one);
        const r = await api<{ url: string }>("/crm/upload-image", { method: "POST", body: fd });
        setPhotos((p) => [...p, r.url]);
        room--;
      } catch (e) {
        toast.error("آپلود تصویر ناموفق بود", e instanceof ApiError ? e.message : one.name);
      }
    }
    setUploading(null);
  }

  const save = useMutation({
    mutationFn: () => {
      const cleanAttrs = Object.fromEntries(
        Object.entries(attrs)
          .filter(([k, v]) => v.trim() !== "" && fields.some((x) => x.key === k))
          .map(([k, v]) => [k, parseDigits(v.trim())]),
      );
      return api("/crm/leads", {
        json: {
          property_title: f.property_title.trim(),
          property_kind: f.property_kind || null,
          listing_type: f.listing_type || null,
          category_name: f.category_name.trim() || null,
          city_name: f.city_name.trim() || null,
          price: rent ? null : f.price,
          deposit: rent ? f.deposit : null,
          rent_price: rent ? f.rent_price : null,
          area: toInt(f.area),
          phone_number: parseDigits(f.phone_number).trim() || null,
          seller_name: f.seller_name.trim() || null,
          property_url: f.property_url.trim() || null,
          status: f.status || "new",
          notes: f.notes.trim() || null,
          images: photos,
          attrs: cleanAttrs,
        },
      });
    },
    onSuccess: () => {
      toast.success("لید جدید ثبت شد");
      qc.invalidateQueries({ queryKey: ["crm", "leads"] });
      qc.invalidateQueries({ queryKey: ["crm", "calls"] });
      reset();
      onOpenChange(false);
    },
    onError: (e) => toast.error("ثبت نشد", e instanceof ApiError ? e.message : undefined),
  });

  const titleErr = tried && !f.property_title.trim() ? "عنوان ملک الزامی است" : undefined;

  return (
    <RingDialog
      open={open}
      onOpenChange={(o) => {
        if (!o) reset();
        onOpenChange(o);
      }}
      icon={Plus}
      title="لید جدید"
      description="ملکی که خارج از دیوار پیدا شده؛ با عکس و مشخصات."
      wide
      footer={
        <>
          <Button
            className="w-full shadow-[0_8px_24px_-8px_rgb(99_102_241/0.8)]"
            disabled={save.isPending || !!uploading}
            onClick={() => {
              setTried(true);
              if (f.property_title.trim()) save.mutate();
            }}
          >
            {save.isPending && <Loader2 className="animate-spin" />} ثبت لید
          </Button>
          <Button variant="ghost" className="w-full" onClick={() => onOpenChange(false)}>انصراف</Button>
        </>
      }
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="عنوان ملک *" htmlFor="al-title" className="sm:col-span-2" error={titleErr}>
          <Input id="al-title" value={f.property_title} aria-invalid={!!titleErr || undefined} onChange={(e) => set("property_title", e.target.value)} placeholder="مثلاً: آپارتمان ۹۰ متری نارمک" />
        </Field>
        <Field label="نوع ملک" htmlFor="al-kind">
          <NativeSelect id="al-kind" value={f.property_kind} onChange={(e) => set("property_kind", e.target.value)}>
            <option value="">— انتخاب —</option>
            {Object.entries(ADD_KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </NativeSelect>
        </Field>
        <Field label="نوع معامله" htmlFor="al-type">
          <NativeSelect id="al-type" value={f.listing_type} onChange={(e) => set("listing_type", e.target.value)}>
            <option value="">— انتخاب —</option>
            <option value="buy">فروش</option>
            <option value="rent">رهن و اجاره</option>
          </NativeSelect>
        </Field>
        {rent ? (
          <>
            <Field label="ودیعه (تومان)" htmlFor="al-deposit">
              <MoneyInput id="al-deposit" value={f.deposit} onChange={(v) => set("deposit", v)} />
            </Field>
            <Field label="اجارهٔ ماهانه (تومان)" htmlFor="al-rent">
              <MoneyInput id="al-rent" value={f.rent_price} onChange={(v) => set("rent_price", v)} />
            </Field>
          </>
        ) : (
          <Field label="قیمت (تومان)" htmlFor="al-price">
            <MoneyInput id="al-price" value={f.price} onChange={(v) => set("price", v)} />
          </Field>
        )}
        <Field label="متراژ (متر)" htmlFor="al-area">
          <Input id="al-area" dir="ltr" inputMode="numeric" value={f.area} onChange={(e) => set("area", e.target.value)} className="text-end tabular" />
        </Field>
        <Field label="شهر" htmlFor="al-city">
          <Input id="al-city" value={f.city_name} onChange={(e) => set("city_name", e.target.value)} />
        </Field>
        <Field label="دسته‌بندی" htmlFor="al-cat">
          <Input id="al-cat" list="al-cat-list" value={f.category_name} onChange={(e) => set("category_name", e.target.value)} />
          <datalist id="al-cat-list">
            {categories.map((c) => <option key={c.name} value={c.name} />)}
          </datalist>
        </Field>
        <Field label="شماره تماس" htmlFor="al-phone">
          <Input id="al-phone" dir="ltr" inputMode="tel" placeholder="09123456789" value={f.phone_number} onChange={(e) => set("phone_number", e.target.value)} className="text-end tabular" />
        </Field>
        <Field label="نام فروشنده / مالک" htmlFor="al-seller">
          <Input id="al-seller" value={f.seller_name} onChange={(e) => set("seller_name", e.target.value)} />
        </Field>
        <Field label="وضعیت" htmlFor="al-status">
          <NativeSelect id="al-status" value={f.status} onChange={(e) => set("status", e.target.value)}>
            {LEAD_STATUS_ORDER.map((s) => <option key={s} value={s}>{LEAD_STATUS[s].label}</option>)}
          </NativeSelect>
        </Field>
        <Field label="لینک آگهی" htmlFor="al-url" className="sm:col-span-2">
          <Input id="al-url" dir="ltr" type="url" placeholder="https://" value={f.property_url} onChange={(e) => set("property_url", e.target.value)} />
        </Field>

        <AnimatePresence initial={false}>
          {fields.length > 0 && (
            <motion.fieldset
              key={f.property_kind}
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              className="overflow-hidden rounded-xl border bg-muted/30 sm:col-span-2"
            >
              <legend className="sr-only">مشخصات {ADD_KINDS[f.property_kind]}</legend>
              <div className="grid grid-cols-2 gap-3 p-3 sm:grid-cols-3">
                <p className="col-span-full text-xs font-bold text-muted-foreground">مشخصات {ADD_KINDS[f.property_kind]}</p>
                {fields.map((x) => {
                  const id = `al-attr-${x.key}`;
                  const v = attrs[x.key] ?? "";
                  const on = (val: string) => setAttrs((a) => ({ ...a, [x.key]: val }));
                  return (
                    <Field key={x.key} label={x.label} htmlFor={id}>
                      {x.type === "bool" ? (
                        <NativeSelect id={id} value={v} onChange={(e) => on(e.target.value)}>
                          <option value="">—</option>
                          <option value="true">دارد</option>
                          <option value="false">ندارد</option>
                        </NativeSelect>
                      ) : x.type === "pick" ? (
                        <NativeSelect id={id} value={v} onChange={(e) => on(e.target.value)}>
                          <option value="">—</option>
                          {x.options?.map((o) => <option key={o} value={o}>{o}</option>)}
                        </NativeSelect>
                      ) : (
                        <Input
                          id={id}
                          value={v}
                          dir={x.type === "num" ? "ltr" : undefined}
                          inputMode={x.type === "num" ? "numeric" : undefined}
                          className={x.type === "num" ? "text-end tabular" : undefined}
                          onChange={(e) => on(e.target.value)}
                        />
                      )}
                    </Field>
                  );
                })}
              </div>
            </motion.fieldset>
          )}
        </AnimatePresence>

        <Field label="یادداشت" htmlFor="al-notes" className="sm:col-span-2">
          <Textarea id="al-notes" rows={2} value={f.notes} onChange={(e) => set("notes", e.target.value)} />
        </Field>

        <div className="grid gap-2 sm:col-span-2">
          <span className="text-sm font-medium">تصاویر</span>
          <div className="flex flex-wrap gap-2">
            <AnimatePresence>
              {photos.map((u, i) => (
                <motion.div
                  key={u}
                  layout
                  initial={{ opacity: 0, scale: 0.8 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.8 }}
                  className="relative size-20 overflow-hidden rounded-xl border bg-muted"
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={u} alt={`تصویر ${faNum(i + 1)}`} className="size-full object-cover" />
                  <button
                    type="button"
                    aria-label={`حذف تصویر ${faNum(i + 1)}`}
                    onClick={() => setPhotos((p) => p.filter((x) => x !== u))}
                    className="absolute end-1 top-1 grid size-6 place-items-center rounded-full bg-black/60 text-white outline-none hover:bg-black/80 focus-visible:ring-2 focus-visible:ring-white"
                  >
                    <X className="size-3.5" />
                  </button>
                </motion.div>
              ))}
            </AnimatePresence>
            {photos.length < MAX_PHOTOS && (
              <button
                type="button"
                onClick={() => file.current?.click()}
                disabled={!!uploading}
                className="grid size-20 place-items-center rounded-xl border border-dashed text-muted-foreground outline-none transition hover:border-primary hover:text-primary focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
              >
                {uploading ? <Loader2 className="size-5 animate-spin" /> : <ImagePlus className="size-5" />}
                <span className="sr-only">افزودن تصویر</span>
              </button>
            )}
          </div>
          <input
            ref={file}
            type="file"
            accept="image/*"
            multiple
            className="sr-only"
            aria-label="انتخاب تصویر"
            tabIndex={-1}
            onChange={(e) => upload(e.target.files)}
          />
          <p className="text-xs text-muted-foreground" role="status">
            {uploading ? `در حال آپلود ${uploading}…` : `تا ${faNum(MAX_PHOTOS)} تصویر؛ اولین تصویر، تصویر اصلی آگهی می‌شود.`}
          </p>
        </div>
      </div>
    </RingDialog>
  );
}
