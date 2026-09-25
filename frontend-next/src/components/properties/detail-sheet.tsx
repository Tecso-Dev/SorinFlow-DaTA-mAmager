"use client";

// The property detail sheet — viewProperty() in the old panel. A wide side
// drawer (not the small RingDialog) because the content is a dozen cards of
// specs, price, AI facts, photos and actions; RingDialog stays for the
// «ملک‌های مشابه» modal it already fits, and for delete confirmation.

import {
  Armchair, Banknote, Check, ExternalLink, FileText, Images, Info, Loader2, MapPin, Minus, Network, Trash2, X,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { cn } from "cn";
import { Empty, ErrorNote, ListSkeleton, NativeSelect, useConfirm } from "@/components/panel/kit";
import { Button } from "@/components/ui/button";
import { Sheet, SheetClose, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { CORNER_OPTIONS, DIRECTION_OPTIONS, price, PROPERTY_KIND } from "@/lib/crm";
import { faNum } from "@/lib/format";
import { AiFactsBlock, AiPhotoTagsBlock } from "./ai-blocks";
import { PhotoCarousel } from "./lightbox";
import { AgencyBadge, DupBadge, formatSerial, listingMoney, NoPhoneCell, PhoneLink, safeUrl } from "./shared";
import type { Property } from "./types";

type Row = { label: string; value: React.ReactNode; wide?: boolean };
const has = (v: unknown) => v !== null && v !== undefined && v !== "";

function Block({ icon: Icon, title, rows, children }: { icon: React.ComponentType<{ className?: string }>; title: string; rows?: Row[]; children?: React.ReactNode }) {
  const list = (rows ?? []).filter((r) => has(r.value));
  if (!list.length && !children) return null;
  return (
    <section className="rounded-xl border bg-background/40 p-3.5">
      <h3 className="mb-2.5 flex items-center gap-1.5 text-[13px] font-bold">
        <Icon className="size-4 text-primary" aria-hidden /> {title}
      </h3>
      {list.length > 0 && (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2.5 sm:grid-cols-3">
          {list.map((r) => (
            <div key={r.label} className={cn("min-w-0", r.wide && "col-span-full")}>
              <dt className="text-[11px] text-muted-foreground">{r.label}</dt>
              <dd className="mt-0.5 text-sm font-medium break-words">{r.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {children}
    </section>
  );
}

const meters = (v: number | null | undefined) => (v ? `${faNum(v)} متر` : null);
const num = (v: number | null | undefined) => (v === null || v === undefined ? null : faNum(v));

function YesNo({ v }: { v: boolean }) {
  return v ? (
    <span className="inline-flex items-center gap-1 text-success"><Check className="size-3.5" aria-hidden />دارد</span>
  ) : (
    <span className="inline-flex items-center gap-1 text-muted-foreground"><Minus className="size-3.5" aria-hidden />ندارد</span>
  );
}

/** جهت ساختمان / نبش — a select that writes straight back with PATCH
 *  /properties/{id}; PropertyUpdate only accepts these two extra fields. */
function InlineSelect({
  propertyId, field, value, options, label,
}: { propertyId: number; field: "building_direction" | "corner_type"; value: string | null; options: string[]; label: string }) {
  const qc = useQueryClient();
  const [v, setV] = useState(value ?? "");
  const all = v && !options.includes(v) ? [v, ...options] : options;
  const m = useMutation({
    mutationFn: (next: string) => api(`/properties/${propertyId}`, { method: "PATCH", json: { [field]: next || null } }),
    onSuccess: (_d, next) => {
      toast.success("ذخیره شد", next ? `${label}: ${next}` : "مقدار پاک شد");
      qc.invalidateQueries({ queryKey: ["properties", propertyId] });
      qc.invalidateQueries({ queryKey: ["properties", "list"] });
    },
    onError: (e) => {
      setV(value ?? "");
      toast.error("ذخیره نشد", e instanceof ApiError ? e.message : undefined);
    },
  });
  return (
    <label className="grid gap-1 text-[11px] text-muted-foreground">
      {label}
      <div className="flex items-center gap-1.5">
        <NativeSelect
          aria-label={`${label} — با انتخاب ذخیره می‌شود`}
          value={v}
          disabled={m.isPending}
          onChange={(e) => { setV(e.target.value); m.mutate(e.target.value); }}
          className="h-8 text-[13px]"
        >
          <option value="">— نامشخص —</option>
          {all.map((o) => <option key={o} value={o}>{o}</option>)}
        </NativeSelect>
        {m.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
      </div>
    </label>
  );
}

export function PropertySheet({
  propertyId, onClose, onOpenMatch, onDeleted,
}: { propertyId: number | null; onClose: () => void; onOpenMatch: (id: number) => void; onDeleted: () => void }) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const q = useQuery({
    queryKey: ["properties", propertyId],
    queryFn: () => api<Property>(`/properties/${propertyId}`),
    enabled: propertyId !== null,
    retry: false,
  });
  const del = useMutation({
    mutationFn: (id: number) => api(`/properties/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("حذف شد");
      qc.invalidateQueries({ queryKey: ["properties", "list"] });
      onDeleted();
    },
    onError: (e) => toast.error("حذف نشد", e instanceof ApiError ? e.message : undefined),
  });

  async function handleDelete() {
    if (!p) return;
    const ok = await confirm({ title: "حذف ملک", description: `«${p.title}» حذف شود؟ این کار برگشت‌پذیر نیست.`, confirm: "حذف", danger: true, icon: Trash2 });
    if (ok) del.mutate(p.id);
  }

  const p = q.data;
  const map = p?.latitude && p?.longitude ? `https://www.google.com/maps?q=${p.latitude},${p.longitude}` : null;
  const extras = Object.entries(p?.extra_attrs ?? {}).filter(([, v]) => has(v));
  const kind = p?.property_type ? PROPERTY_KIND[p.property_type] ?? p.property_type : null;
  const url = safeUrl(p?.url);

  return (
    <Sheet open={propertyId !== null} onOpenChange={(o) => !o && onClose()}>
      <SheetContent
        side="left"
        showCloseButton={false}
        className="gap-0 p-0 data-[side=left]:w-full data-[side=left]:border-e data-[side=left]:sm:max-w-2xl dark:bg-linear-to-b dark:from-primary/[0.06] dark:to-popover"
      >
        <div className="sticky top-0 z-10 border-b bg-popover/90 px-4 py-3 backdrop-blur sm:px-5">
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              {p ? (
                <div className="grid gap-1.5 pe-2">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-mono text-[11px] tabular text-primary">{formatSerial(p.serial_no)}</span>
                    <AgencyBadge p={p} />
                    <DupBadge of={p.ai_duplicate_of} />
                  </div>
                  <SheetTitle className="text-lg leading-7 font-black">{p.title}</SheetTitle>
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                    <span className="font-bold text-primary">{listingMoney(p)}</span>
                    {p.phone_number ? <PhoneLink phone={p.phone_number} /> : <NoPhoneCell p={p} />}
                  </div>
                </div>
              ) : (
                <SheetTitle className="text-lg font-black">جزئیات ملک</SheetTitle>
              )}
              <SheetDescription className="sr-only">جزئیات ملک</SheetDescription>
            </div>
            <SheetClose asChild>
              <Button variant="ghost" size="icon-sm" aria-label="بستن"><X /></Button>
            </SheetClose>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-5">
          {q.isLoading ? (
            <ListSkeleton rows={6} />
          ) : q.isError ? (
            <ErrorNote error={q.error} />
          ) : !p ? null : (
            <div className="grid gap-3">
              <div className="flex flex-wrap gap-2">
                {url && (
                  <Button asChild variant="outline" size="sm">
                    <a href={url} target="_blank" rel="noopener noreferrer"><ExternalLink /> مشاهده در دیوار</a>
                  </Button>
                )}
                <Button variant="outline" size="sm" onClick={() => onOpenMatch(p.id)}><Network /> ملک‌های مشابه</Button>
                <Button variant="destructive" size="sm" disabled={del.isPending} onClick={handleDelete}>
                  {del.isPending ? <Loader2 className="animate-spin" /> : <Trash2 />} حذف
                </Button>
              </div>

              <Block icon={Images} title={`تصاویر (${faNum((p.images ?? []).length)})`}>
                <PhotoCarousel images={p.images} />
              </Block>

              <AiPhotoTagsBlock propertyId={p.id} />

              <Block
                icon={Info}
                title="اطلاعات پایه"
                rows={[
                  { label: "کد ملک", value: <span className="font-mono">{formatSerial(p.serial_no)} · {p.tag_number}</span> },
                  { label: "شناسهٔ دیوار", value: p.divar_id },
                  { label: "نوع آگهی", value: p.listing_type === "rent" ? "🏠 اجاره" : p.listing_type === "buy" ? "💰 خرید" : null },
                  { label: "نوع ملک", value: kind },
                  { label: "دسته‌بندی", value: p.category_name },
                  { label: "دارای تصویر", value: p.has_images ? "✅" : "❌" },
                ]}
              />

              <Block
                icon={Banknote}
                title="اطلاعات قیمت"
                rows={[
                  { label: "قیمت کل", value: p.total_price ? price(p.total_price) : null },
                  { label: "قیمت هر متر", value: p.price_per_meter ? price(p.price_per_meter) : null },
                  { label: "اجارهٔ ماهانه", value: p.rent_price ? price(p.rent_price) : null },
                  { label: "ودیعه", value: p.deposit ? price(p.deposit) : null },
                ]}
              />

              <Block
                icon={Armchair}
                title="مشخصات ملک"
                rows={[
                  { label: "متراژ", value: meters(p.area) },
                  { label: "متراژ زمین", value: meters(p.land_area) },
                  { label: "زیربنا", value: meters(p.built_area) },
                  { label: "تعداد اتاق", value: num(p.rooms) },
                  { label: "طبقه", value: num(p.floor) },
                  { label: "کل طبقات", value: num(p.total_floors) },
                  { label: "سال ساخت", value: p.year_built ? faNum(p.year_built, { useGrouping: false }) : null },
                  { label: "سن بنا", value: p.building_age },
                  { label: "بر", value: meters(p.frontage) },
                  { label: "وضعیت واحد", value: p.unit_status },
                  { label: "نوع سند", value: p.document_type },
                  { label: "نوع کاربری", value: p.usage_type },
                ]}
              >
                <div className="mt-3 grid grid-cols-1 gap-3 border-t pt-3 sm:grid-cols-2">
                  <InlineSelect propertyId={p.id} field="building_direction" value={p.building_direction} options={DIRECTION_OPTIONS} label="جهت ساختمان" />
                  <InlineSelect propertyId={p.id} field="corner_type" value={p.corner_type} options={CORNER_OPTIONS} label="نبش" />
                </div>
              </Block>

              <Block
                icon={Armchair}
                title="امکانات"
                rows={([
                  ["آسانسور", p.has_elevator], ["پارکینگ", p.has_parking], ["انباری", p.has_storage], ["بالکن", p.has_balcony],
                ] as const).map(([label, v]) => ({ label, value: <YesNo v={v} /> }))}
              />

              {extras.length > 0 && (
                <Block icon={Info} title="مشخصات تکمیلی" rows={extras.map(([k, v]) => ({ label: k, value: v === true ? "دارد" : v === false ? "ندارد" : String(v) }))} />
              )}

              <AiFactsBlock property={p} />

              <Block
                icon={MapPin}
                title="موقعیت مکانی"
                rows={[
                  { label: "شهر", value: p.city_name },
                  { label: "منطقه", value: p.district },
                  { label: "محله", value: p.neighborhood },
                  { label: "آدرس", value: p.address, wide: true },
                ]}
              >
                {map && (
                  <a href={map} target="_blank" rel="noopener noreferrer" className="mt-2 inline-flex items-center gap-1 text-sm font-semibold text-primary hover:underline">
                    <MapPin className="size-4" aria-hidden /> مشاهده در نقشه
                  </a>
                )}
              </Block>

              <Block icon={FileText} title="توضیحات">
                {p.description ? <p className="text-sm leading-7 whitespace-pre-wrap">{p.description}</p> : <Empty>—</Empty>}
              </Block>

              <Block
                icon={Info}
                title="اطلاعات تماس"
                rows={[
                  { label: "شماره تماس", value: p.phone_number ? <PhoneLink phone={p.phone_number} /> : <NoPhoneCell p={p} /> },
                  { label: "فروشنده", value: p.seller_name },
                ]}
              />

              <Block
                icon={Info}
                title="اطلاعات ثبت"
                rows={[
                  { label: "اسکرپ شده", value: p.scraped_at ? new Date(p.scraped_at).toLocaleString("fa-IR") : null },
                  { label: "آخرین بروزرسانی", value: p.updated_at ? new Date(p.updated_at).toLocaleString("fa-IR") : null },
                ]}
              />
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
