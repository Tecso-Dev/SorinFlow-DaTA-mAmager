"use client";

// The full property block a lead drawer (and the property sheet) shows: the
// photos, then specs / prices / amenities / extras / location, the same data
// as the old panel's _renderPropertyDetails. «جهت» and «نبش» are filled in by
// people, so they are selects that save as they change.

import {
  Armchair, Banknote, Check, FileText, Images, Info, Loader2, MapPin, Minus, Sparkles, type LucideIcon,
} from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { cn } from "cn";
import { NativeSelect } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { CORNER_OPTIONS, DIRECTION_OPTIONS, PROPERTY_KIND, price } from "@/lib/crm";
import { faNum } from "@/lib/format";
import { PhotoStrip } from "./lightbox";
import { AgencyBadge, LEAD_ATTR_FA, SerialBadge } from "./shared";
import type { PropertyDetail } from "./types";

type Row = { label: string; value: React.ReactNode; wide?: boolean };

function has(v: unknown) {
  return v !== null && v !== undefined && v !== "";
}

function Block({ icon: Icon, title, rows, children }: { icon: LucideIcon; title: string; rows?: Row[]; children?: React.ReactNode }) {
  const list = (rows ?? []).filter((r) => has(r.value));
  if (!list.length && !children) return null;
  return (
    <section className="rounded-xl border bg-background/40 p-3.5">
      <h3 className="mb-2.5 flex items-center gap-1.5 text-[13px] font-bold">
        <Icon className="size-4 text-primary" aria-hidden />
        {title}
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

function YesNo({ v }: { v: boolean | null | undefined }) {
  if (v === null || v === undefined) return null;
  return v ? (
    <span className="inline-flex items-center gap-1 text-success"><Check className="size-3.5" aria-hidden />دارد</span>
  ) : (
    <span className="inline-flex items-center gap-1 text-muted-foreground"><Minus className="size-3.5" aria-hidden />ندارد</span>
  );
}

/** A select that writes straight back to the property (PATCH /properties/{id}).
 *  A free-typed value already on the record stays as an extra option. */
export function PropertyFieldSelect({
  propertyId, field, value, options, label, invalidate,
}: {
  propertyId: number; field: "building_direction" | "corner_type"; value: string | null; options: string[]; label: string;
  invalidate?: unknown[][];
}) {
  const qc = useQueryClient();
  const [v, setV] = useState(value ?? "");
  const all = v && !options.includes(v) ? [v, ...options] : options;
  const m = useMutation({
    mutationFn: (next: string) => api(`/properties/${propertyId}`, { method: "PATCH", json: { [field]: next || null } }),
    onSuccess: (_d, next) => {
      toast.success("ذخیره شد", next ? `${label}: ${next}` : "مقدار پاک شد");
      for (const k of invalidate ?? []) qc.invalidateQueries({ queryKey: k });
    },
    onError: (e) => {
      setV(value ?? "");
      toast.error("ذخیره نشد", e instanceof ApiError ? e.message : undefined);
    },
  });
  return (
    <div className="flex items-center gap-1.5">
      <NativeSelect
        aria-label={`${label} — با انتخاب ذخیره می‌شود`}
        value={v}
        disabled={m.isPending}
        onChange={(e) => {
          setV(e.target.value);
          m.mutate(e.target.value);
        }}
        className="h-8 text-[13px]"
      >
        <option value="">— نامشخص —</option>
        {all.map((o) => <option key={o} value={o}>{o}</option>)}
      </NativeSelect>
      {m.isPending && <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />}
    </div>
  );
}

export function PropertyDetails({ p, invalidate, editable = true, photos = true }: { p: PropertyDetail; invalidate?: unknown[][]; editable?: boolean; photos?: boolean }) {
  const kind = p.property_type ? PROPERTY_KIND[p.property_type] ?? p.property_type : null;
  const images = p.images ?? [];
  const extras = Object.entries(p.extra_attrs ?? {}).filter(([, v]) => has(v));
  const map = p.latitude && p.longitude ? `https://www.google.com/maps?q=${p.latitude},${p.longitude}` : null;

  return (
    <div className="grid gap-3">
      {photos && images.length > 0 && (
        <Block icon={Images} title={`تصاویر (${faNum(images.length)})`}>
          <PhotoStrip images={images} />
        </Block>
      )}
      <Block
        icon={Info}
        title="مشخصات ملک"
        rows={[
          { label: "کد ملک", value: p.serial_no !== null ? <SerialBadge serial={p.serial_no} /> : null },
          { label: "نوع ملک", value: kind },
          { label: "دسته‌بندی", value: p.category_name },
          { label: "متراژ", value: meters(p.area) },
          { label: "متراژ زمین", value: meters(p.land_area) },
          { label: "زیربنا", value: meters(p.built_area) },
          { label: "تعداد اتاق", value: num(p.rooms) },
          { label: "طبقه", value: num(p.floor) },
          { label: "کل طبقات", value: p.total_floors ? num(p.total_floors) : null },
          { label: "سال ساخت", value: p.year_built ? faNum(p.year_built, { useGrouping: false }) : null },
          { label: "سن بنا", value: p.building_age },
          { label: "بر", value: meters(p.frontage) },
          { label: "وضعیت واحد", value: p.unit_status },
          { label: "نوع سند", value: p.document_type },
          { label: "نوع کاربری", value: p.usage_type },
          { label: "آگهی‌دهنده (دیوار)", value: p.advertiser_type === "agency" ? "مشاور املاک" : p.advertiser_type === "personal" ? "شخصی" : null },
          {
            label: "آگهی‌دهنده (متن آگهی)",
            value: p.agency_suspected ? (
              <span className="grid gap-0.5">
                <span className="flex items-center gap-1">مشاور املاک <AgencyBadge p={p} /></span>
                {p.agency_evidence && <span className="text-[11px] text-muted-foreground">«{p.agency_evidence}»</span>}
              </span>
            ) : null,
          },
        ]}
      >
        {/* always shown, even empty: these two are meant to be filled in */}
        <div className="mt-3 grid grid-cols-1 gap-3 border-t pt-3 sm:grid-cols-2">
          {editable ? (
            <>
              <label className="grid gap-1 text-[11px] text-muted-foreground">
                جهت ساختمان
                <PropertyFieldSelect propertyId={p.id} field="building_direction" value={p.building_direction} options={DIRECTION_OPTIONS} label="جهت ساختمان" invalidate={invalidate} />
              </label>
              <label className="grid gap-1 text-[11px] text-muted-foreground">
                نبش
                <PropertyFieldSelect propertyId={p.id} field="corner_type" value={p.corner_type} options={CORNER_OPTIONS} label="نبش" invalidate={invalidate} />
              </label>
            </>
          ) : (
            <>
              <div><div className="text-[11px] text-muted-foreground">جهت ساختمان</div><div className="text-sm font-medium">{p.building_direction || "—"}</div></div>
              <div><div className="text-[11px] text-muted-foreground">نبش</div><div className="text-sm font-medium">{p.corner_type || "—"}</div></div>
            </>
          )}
        </div>
      </Block>
      <Block
        icon={Banknote}
        title="اطلاعات قیمت"
        rows={[
          { label: "قیمت کل", value: p.total_price ? price(p.total_price) : null },
          { label: "قیمت هر متر", value: p.price_per_meter ? price(p.price_per_meter) : null },
          { label: "ودیعه", value: p.deposit ? price(p.deposit) : null },
          { label: "اجاره ماهانه", value: p.rent_price ? price(p.rent_price) : null },
        ]}
      />
      <Block
        icon={Armchair}
        title="امکانات"
        rows={([
          ["آسانسور", p.has_elevator],
          ["پارکینگ", p.has_parking],
          ["انباری", p.has_storage],
          ["بالکن", p.has_balcony],
        ] as const).map(([label, v]) => ({ label, value: has(v) ? <YesNo v={v} /> : null }))}
      />
      <Block
        icon={Sparkles}
        title="مشخصات تکمیلی"
        rows={extras.map(([k, v]) => ({
          label: LEAD_ATTR_FA[k] ?? k,
          value: v === true || v === "true" ? "دارد" : v === false || v === "false" ? "ندارد" : String(v),
        }))}
      />
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
      {p.description && (
        <Block icon={FileText} title="توضیحات">
          <p className="text-sm leading-7 whitespace-pre-wrap">{p.description}</p>
        </Block>
      )}
    </div>
  );
}
