"use client";

// Search, tag filter, the «جستجوی پیشرفته» popover and the archived switch —
// everything above the card grid that narrows GET /filing/files.

import { ListFilter, Search, X } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Switch } from "@/components/ui/switch";
import { Field, NativeSelect } from "@/components/panel/kit";
import { faNum } from "@/lib/format";
import { EMPTY_ADVANCED, type AdvancedFilters } from "./types";

const activeCount = (a: AdvancedFilters) =>
  Object.entries(a).filter(([k, v]) => (typeof v === "boolean" ? v : v !== "" && k)).length;

export function FilingFilters({
  search, onSearch, tags, tag, onTag, archived, onArchived, advanced, onAdvanced,
}: {
  search: string;
  onSearch: (v: string) => void;
  tags: { name: string; count: number }[];
  tag: string;
  onTag: (v: string) => void;
  archived: boolean;
  onArchived: (v: boolean) => void;
  advanced: AdvancedFilters;
  onAdvanced: (a: AdvancedFilters) => void;
}) {
  const [draft, setDraft] = useState(advanced);
  const [text, setText] = useState(search);
  const count = activeCount(advanced);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <form
        className="relative"
        onSubmit={(e) => {
          e.preventDefault();
          onSearch(text);
        }}
      >
        <Search className="pointer-events-none absolute inset-y-0 start-2.5 my-auto size-4 text-muted-foreground" />
        <Input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="جستجو در عنوان، آدرس، تگ، شماره…"
          className="w-56 ps-8"
          aria-label="جستجوی فایل"
        />
      </form>

      <NativeSelect value={tag} onChange={(e) => onTag(e.target.value)} aria-label="فیلتر برچسب" className="h-8 w-36">
        <option value="">همهٔ برچسب‌ها</option>
        {tags.map((t) => <option key={t.name} value={t.name}>{t.name} ({faNum(t.count)})</option>)}
      </NativeSelect>

      <Popover onOpenChange={(o) => o && setDraft(advanced)}>
        <PopoverTrigger asChild>
          <Button variant="outline" size="sm" className="gap-1.5">
            <ListFilter className="size-4" /> فیلترهای پیشرفته
            {count > 0 && <span className="rounded-full bg-primary px-1.5 text-[10px] text-primary-foreground">{faNum(count)}</span>}
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-80 p-3">
          <div className="grid grid-cols-2 gap-2.5">
            <Field label="قیمت از" htmlFor="ff-pmin">
              <Input id="ff-pmin" dir="ltr" inputMode="numeric" className="tabular" value={draft.price_min} onChange={(e) => setDraft({ ...draft, price_min: e.target.value.replace(/\D/g, "") })} />
            </Field>
            <Field label="قیمت تا" htmlFor="ff-pmax">
              <Input id="ff-pmax" dir="ltr" inputMode="numeric" className="tabular" value={draft.price_max} onChange={(e) => setDraft({ ...draft, price_max: e.target.value.replace(/\D/g, "") })} />
            </Field>
            <Field label="متراژ از" htmlFor="ff-amin">
              <Input id="ff-amin" dir="ltr" inputMode="numeric" className="tabular" value={draft.area_min} onChange={(e) => setDraft({ ...draft, area_min: e.target.value.replace(/\D/g, "") })} />
            </Field>
            <Field label="متراژ تا" htmlFor="ff-amax">
              <Input id="ff-amax" dir="ltr" inputMode="numeric" className="tabular" value={draft.area_max} onChange={(e) => setDraft({ ...draft, area_max: e.target.value.replace(/\D/g, "") })} />
            </Field>
            <Field label="حداقل اتاق" htmlFor="ff-rooms">
              <Input id="ff-rooms" dir="ltr" inputMode="numeric" className="tabular" value={draft.rooms_min} onChange={(e) => setDraft({ ...draft, rooms_min: e.target.value.replace(/\D/g, "") })} />
            </Field>
            <Field label="منطقه" htmlFor="ff-district">
              <Input id="ff-district" value={draft.district} onChange={(e) => setDraft({ ...draft, district: e.target.value })} />
            </Field>
            <Field label="نوع ملک" htmlFor="ff-type" className="col-span-2">
              <Input id="ff-type" value={draft.property_type} onChange={(e) => setDraft({ ...draft, property_type: e.target.value })} placeholder="آپارتمان، ویلایی، …" />
            </Field>
            <Field label="نوع آگهی" htmlFor="ff-listing" className="col-span-2">
              <NativeSelect id="ff-listing" value={draft.listing_type} onChange={(e) => setDraft({ ...draft, listing_type: e.target.value })}>
                <option value="">همه</option>
                <option value="buy">خرید و فروش</option>
                <option value="rent">رهن و اجاره</option>
              </NativeSelect>
            </Field>
          </div>
          <div className="mt-2.5 flex flex-wrap gap-3 text-sm">
            {([["has_elevator", "آسانسور"], ["has_parking", "پارکینگ"], ["has_storage", "انباری"]] as const).map(([k, label]) => (
              <label key={k} className="flex items-center gap-1.5">
                <input type="checkbox" className="size-4 accent-primary" checked={draft[k]} onChange={(e) => setDraft({ ...draft, [k]: e.target.checked })} />
                {label}
              </label>
            ))}
          </div>
          <div className="mt-3 flex gap-2">
            <Button size="sm" className="flex-1" onClick={() => onAdvanced(draft)}>اعمال فیلتر</Button>
            <Button size="sm" variant="ghost" onClick={() => { setDraft(EMPTY_ADVANCED); onAdvanced(EMPTY_ADVANCED); }}>پاک کردن</Button>
          </div>
        </PopoverContent>
      </Popover>

      {(search || tag || count > 0) && (
        <Button
          variant="ghost" size="sm" className="gap-1 text-muted-foreground"
          onClick={() => { setText(""); onSearch(""); onTag(""); onAdvanced(EMPTY_ADVANCED); }}
        >
          <X className="size-3.5" /> پاک کردن فیلترها
        </Button>
      )}

      <label className="ms-auto flex items-center gap-2 text-sm text-muted-foreground">
        بایگانی‌شده‌ها
        <Switch checked={archived} onCheckedChange={onArchived} />
      </label>
    </div>
  );
}
