"use client";

// «فیلترهای بیشتر»: everything optional, behind one collapsible — but it
// opens itself the moment any filter inside it is set, so a filter can
// never be applied out of sight (the old panel's mobile lesson, kept on
// purpose). Buy shows the price band, rent shows deposit/monthly, and the
// area/room/advertiser/feature/date filters are common to both.

import { ChevronDown } from "lucide-react";
import { useState } from "react";
import { cn } from "cn";
import { JalaliDateInput } from "@/components/panel/date-input";
import { Field, NativeSelect } from "@/components/panel/kit";
import { Checkbox } from "@/components/ui/checkbox";
import { faNum } from "@/lib/format";
import { DigitsInput } from "./digits-input";
import type { Category } from "./types";
import type { ScrapeFormState } from "./use-scrape-form";

const FEATURES: { key: "hasElevator" | "hasParking" | "hasStorage" | "hasBalcony"; label: string }[] = [
  { key: "hasElevator", label: "آسانسور" },
  { key: "hasParking", label: "پارکینگ" },
  { key: "hasStorage", label: "انباری" },
  { key: "hasBalcony", label: "بالکن" },
];

export function MoreFilters({
  state, set, categoryType, activeCount,
}: {
  state: ScrapeFormState;
  set: <K extends keyof ScrapeFormState>(key: K, value: ScrapeFormState[K]) => void;
  categoryType: Category["type"] | undefined;
  activeCount: number;
}) {
  const [open, setOpen] = useState(activeCount > 0);
  // opens itself the moment a filter becomes active; never auto-closes on
  // its own. Adjusted during render (not an effect) so it takes effect the
  // same render a filter is set, with no extra pass.
  const [seenActive, setSeenActive] = useState(activeCount > 0);
  const isActive = activeCount > 0;
  if (isActive !== seenActive) {
    setSeenActive(isActive);
    if (isActive) setOpen(true);
  }

  return (
    <div className={cn("rounded-xl border", activeCount > 0 && "border-primary/40")}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls="scraper-more-body"
        className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-sm font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span>
          فیلترهای بیشتر
          {activeCount > 0 && <span className="text-primary"> · {faNum(activeCount)} فعال</span>}
        </span>
        <ChevronDown className={cn("size-4 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div id="scraper-more-body" className="grid gap-3 border-t p-3">
          {categoryType === "buy" && (
            <div className="grid gap-2 sm:grid-cols-2">
              <Field label="قیمت کل (تومان)">
                <div className="flex gap-2">
                  <DigitsInput aria-label="قیمت از" placeholder="از" value={state.minPrice} onChange={(v) => set("minPrice", v)} />
                  <DigitsInput aria-label="قیمت تا" placeholder="تا" value={state.maxPrice} onChange={(v) => set("maxPrice", v)} />
                </div>
              </Field>
              <Field label="قیمت هر متر مربع (تومان)">
                <div className="flex gap-2">
                  <DigitsInput aria-label="قیمت هر متر از" placeholder="از" value={state.minPricePerMeter} onChange={(v) => set("minPricePerMeter", v)} />
                  <DigitsInput aria-label="قیمت هر متر تا" placeholder="تا" value={state.maxPricePerMeter} onChange={(v) => set("maxPricePerMeter", v)} />
                </div>
              </Field>
            </div>
          )}
          {categoryType === "rent" && (
            <div className="grid gap-2 sm:grid-cols-2">
              <Field label="ودیعه (تومان)">
                <div className="flex gap-2">
                  <DigitsInput aria-label="ودیعه از" placeholder="از" value={state.minDeposit} onChange={(v) => set("minDeposit", v)} />
                  <DigitsInput aria-label="ودیعه تا" placeholder="تا" value={state.maxDeposit} onChange={(v) => set("maxDeposit", v)} />
                </div>
              </Field>
              <Field label="اجاره ماهانه (تومان)">
                <div className="flex gap-2">
                  <DigitsInput aria-label="اجاره از" placeholder="از" value={state.minRent} onChange={(v) => set("minRent", v)} />
                  <DigitsInput aria-label="اجاره تا" placeholder="تا" value={state.maxRent} onChange={(v) => set("maxRent", v)} />
                </div>
              </Field>
            </div>
          )}

          <div className="grid gap-2 sm:grid-cols-2">
            <Field label="متراژ (متر)">
              <div className="flex gap-2">
                <DigitsInput aria-label="متراژ از" placeholder="از" value={state.minArea} onChange={(v) => set("minArea", v)} />
                <DigitsInput aria-label="متراژ تا" placeholder="تا" value={state.maxArea} onChange={(v) => set("maxArea", v)} />
              </div>
            </Field>
            <Field label="تعداد اتاق">
              <div className="flex gap-2">
                <DigitsInput aria-label="اتاق از" placeholder="از" value={state.minRooms} onChange={(v) => set("minRooms", v)} />
                <DigitsInput aria-label="اتاق تا" placeholder="تا" value={state.maxRooms} onChange={(v) => set("maxRooms", v)} />
              </div>
            </Field>
          </div>

          <Field
            label="آگهی‌دهنده"
            hint="با انتخاب «شخصی» یا «مشاور املاک»، آگهی‌هایی که نوع آگهی‌دهنده‌شان مشخص نیست ذخیره نمی‌شوند — پس نتیجه کمتر ولی دقیق است."
          >
            <NativeSelect
              aria-label="آگهی‌دهنده"
              value={state.advertiserType}
              onChange={(e) => set("advertiserType", e.target.value as ScrapeFormState["advertiserType"])}
            >
              <option value="">همه</option>
              <option value="personal">شخصی</option>
              <option value="agency">مشاور املاک</option>
            </NativeSelect>
          </Field>

          <fieldset className="grid gap-1.5">
            <legend className="text-sm font-medium">ویژگی‌ها</legend>
            <div className="flex flex-wrap gap-3">
              <label className="flex items-center gap-1.5 text-sm">
                <Checkbox checked={state.hasImages} onCheckedChange={(v) => set("hasImages", v === true)} /> دارای عکس
              </label>
              {FEATURES.map((f) => (
                <label key={f.key} className="flex items-center gap-1.5 text-sm">
                  <Checkbox checked={state[f.key]} onCheckedChange={(v) => set(f.key, v === true)} /> {f.label}
                </label>
              ))}
            </div>
          </fieldset>

          <Field label="تاریخ انتشار آگهی (شمسی)" hint="با انتخاب تاریخ، فقط آگهی‌های منتشرشده در همان روز اسکرپ می‌شوند">
            <div className="flex gap-1.5">
              <JalaliDateInput aria-label="تاریخ انتشار آگهی" value={state.postedDate} onChange={(d) => set("postedDate", d)} className="flex-1" />
            </div>
          </Field>
        </div>
      )}
    </div>
  );
}
