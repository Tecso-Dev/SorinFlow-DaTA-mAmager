"use client";

// «لیست املاک» — everything the old panel's properties section did: search,
// the searchable city picker, a buy/rent category select with a derived
// rent-only band, pagination, both exports, the detail sheet, the similar-
// properties modal and delete. Filters commit on «جستجو» (or Enter in the
// search box) or a page change — the same "read whatever is in the controls
// right now" behaviour the old DOM-driven panel had, not a live-as-you-type
// search.

import {
  Building2, ExternalLink, Eye, FileJson, FileSpreadsheet, Search, SlidersHorizontal, Trash2,
} from "lucide-react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { CountUp, Reveal, Tilt } from "@/components/viz";
import {
  Empty, ErrorNote, Field, ListSkeleton, NativeSelect, PageHeader, Pagination, Section, Toolbar, useConfirm,
} from "@/components/panel/kit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { exportHref, price, qs } from "@/lib/crm";
import { faNum } from "@/lib/format";
import { CityPicker } from "./city-picker";
import { PropertySheet } from "./detail-sheet";
import { MatchDialog } from "./match-dialog";
import { AgencyBadge, DupBadge, formatSerial, NoPhoneCell, PhoneLink, safeUrl } from "./shared";
import type { Category, City, PropertyPage } from "./types";

const SIZE = 20;

type Filters = {
  search: string;
  city: string;
  category: string;
  minDeposit: string;
  maxDeposit: string;
  minRent: string;
  maxRent: string;
  minPrice: string;
  maxPrice: string;
  minArea: string;
  maxArea: string;
  minRooms: string;
  maxRooms: string;
  hasPhone: string; // "" | "1" | "0"
  sortBy: string;
  sortOrder: "asc" | "desc";
};

const EMPTY_FILTERS: Filters = {
  search: "", city: "", category: "", minDeposit: "", maxDeposit: "", minRent: "", maxRent: "",
  minPrice: "", maxPrice: "", minArea: "", maxArea: "", minRooms: "", maxRooms: "",
  hasPhone: "", sortBy: "scraped_at", sortOrder: "desc",
};

const SORT_OPTIONS: { value: string; label: string }[] = [
  { value: "scraped_at", label: "تاریخ اسکرپ" },
  { value: "price", label: "قیمت" },
  { value: "area", label: "متراژ" },
  { value: "rooms", label: "تعداد اتاق" },
];

function truncate(title: string, n: number) {
  return title.length > n ? `${title.slice(0, n)}…` : title;
}

export function PropertiesView() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [draft, setDraft] = useState<Filters>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<Filters>(EMPTY_FILTERS);
  const [page, setPage] = useState(1);
  const [more, setMore] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);
  const [matchId, setMatchId] = useState<number | null>(null);

  const cities = useQuery({ queryKey: ["scraper", "cities"], queryFn: () => api<City[]>("/scraper/cities"), staleTime: 10 * 60_000 });
  const categories = useQuery({ queryKey: ["scraper", "categories"], queryFn: () => api<Category[]>("/scraper/categories"), staleTime: 10 * 60_000 });

  // The category's own type decides buy/rent, and only then is it sent.
  const draftType = categories.data?.find((c) => c.name === draft.category)?.type ?? null;
  const appliedType = categories.data?.find((c) => c.name === applied.category)?.type ?? null;

  const params = useMemo(() => ({
    page, size: SIZE,
    search: applied.search || null,
    city: applied.city || null,
    category: applied.category || null,
    listing_type: appliedType,
    min_deposit: applied.minDeposit || null,
    max_deposit: applied.maxDeposit || null,
    min_rent_price: applied.minRent || null,
    max_rent_price: applied.maxRent || null,
    min_price: applied.minPrice || null,
    max_price: applied.maxPrice || null,
    min_area: applied.minArea || null,
    max_area: applied.maxArea || null,
    min_rooms: applied.minRooms || null,
    max_rooms: applied.maxRooms || null,
    has_phone: applied.hasPhone || null,
    sort_by: applied.sortBy,
    sort_order: applied.sortOrder,
  }), [applied, appliedType, page]);

  const list = useQuery({
    queryKey: ["properties", "list", params],
    queryFn: () => api<PropertyPage>(`/properties${qs(params)}`),
    placeholderData: keepPreviousData,
  });

  const del = useMutation({
    mutationFn: (id: number) => api(`/properties/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("حذف شد");
      qc.invalidateQueries({ queryKey: ["properties", "list"] });
    },
    onError: (e) => toast.error("حذف نشد", e instanceof ApiError ? e.message : undefined),
  });

  const jsonExport = useMutation({
    mutationFn: () => api<{ data: unknown[]; format: string }>("/properties/export", {
      method: "POST",
      json: { city: applied.city || null, listing_type: appliedType },
    }),
    onSuccess: (r) => {
      const blob = new Blob([JSON.stringify(r.data, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "properties-export.json";
      a.click();
      URL.revokeObjectURL(a.href);
      toast.success("خروجی JSON آماده شد", `${faNum(r.data.length)} ردیف`);
    },
    onError: (e) => toast.error("خروجی ناموفق بود", e instanceof ApiError ? e.message : undefined),
  });

  async function handleDelete(id: number, title: string) {
    const ok = await confirm({ title: "حذف ملک", description: `«${title}» حذف شود؟ این کار برگشت‌پذیر نیست.`, confirm: "حذف", danger: true, icon: Trash2 });
    if (ok) del.mutate(id);
  }

  function applyFilters() {
    setApplied(draft);
    setPage(1);
  }

  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;
  const pages = list.data?.pages ?? 1;
  const excelHref = exportHref("/properties/export/excel", {
    city: applied.city || null, category: applied.category || null, search: applied.search || null,
    listing_type: appliedType, min_deposit: applied.minDeposit || null, max_deposit: applied.maxDeposit || null,
    min_rent_price: applied.minRent || null, max_rent_price: applied.maxRent || null,
  });

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        icon={Building2}
        title="لیست املاک"
        hint="همهٔ آگهی‌های اسکرپ‌شده، با فیلتر شهر و دسته‌بندی"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button asChild variant="outline" size="sm">
              <a href={excelHref} download><FileSpreadsheet /> خروجی اکسل</a>
            </Button>
            <Button variant="outline" size="sm" disabled={jsonExport.isPending} onClick={() => jsonExport.mutate()}>
              <FileJson /> خروجی JSON
            </Button>
          </div>
        }
      />

      <Reveal>
        <Tilt className="w-fit">
          <div className="flex items-center gap-3 rounded-2xl border bg-card px-4 py-3 shadow-sm dark:bg-linear-to-b dark:from-white/[0.04] dark:to-transparent dark:shadow-none">
            <div className="grid size-9 place-items-center rounded-xl bg-primary/12 text-primary"><Building2 className="size-4.5" /></div>
            <div>
              <div className="text-[26px] leading-none font-black tabular"><CountUp value={total} /></div>
              <div className="text-[11px] text-muted-foreground">ملک یافت‌شده</div>
            </div>
          </div>
        </Tilt>
      </Reveal>

      <Section>
        <Toolbar className="mb-3">
          <Field label="جستجو" htmlFor="pf-search" className="min-w-48 flex-1">
            <Input
              id="pf-search"
              value={draft.search}
              onChange={(e) => setDraft((d) => ({ ...d, search: e.target.value }))}
              onKeyDown={(e) => e.key === "Enter" && applyFilters()}
              placeholder="عنوان، منطقه یا توضیحات…"
            />
          </Field>
          <Field label="شهر" htmlFor="pf-city">
            <CityPicker id="pf-city" cities={cities.data ?? []} value={draft.city} onChange={(city) => setDraft((d) => ({ ...d, city }))} />
          </Field>
          <Field label="دسته‌بندی" htmlFor="pf-category">
            <NativeSelect id="pf-category" value={draft.category} onChange={(e) => setDraft((d) => ({ ...d, category: e.target.value }))} className="min-w-40">
              <option value="">همهٔ دسته‌ها</option>
              {(categories.data ?? []).map((c) => <option key={c.slug} value={c.name}>{c.name}</option>)}
            </NativeSelect>
          </Field>
          {draftType === "rent" && (
            <>
              <Field label="حداقل ودیعه" htmlFor="pf-min-deposit">
                <Input id="pf-min-deposit" dir="ltr" inputMode="numeric" className="w-32 tabular" value={draft.minDeposit} onChange={(e) => setDraft((d) => ({ ...d, minDeposit: e.target.value.replace(/\D/g, "") }))} />
              </Field>
              <Field label="حداکثر ودیعه" htmlFor="pf-max-deposit">
                <Input id="pf-max-deposit" dir="ltr" inputMode="numeric" className="w-32 tabular" value={draft.maxDeposit} onChange={(e) => setDraft((d) => ({ ...d, maxDeposit: e.target.value.replace(/\D/g, "") }))} />
              </Field>
              <Field label="حداقل اجاره" htmlFor="pf-min-rent">
                <Input id="pf-min-rent" dir="ltr" inputMode="numeric" className="w-32 tabular" value={draft.minRent} onChange={(e) => setDraft((d) => ({ ...d, minRent: e.target.value.replace(/\D/g, "") }))} />
              </Field>
              <Field label="حداکثر اجاره" htmlFor="pf-max-rent">
                <Input id="pf-max-rent" dir="ltr" inputMode="numeric" className="w-32 tabular" value={draft.maxRent} onChange={(e) => setDraft((d) => ({ ...d, maxRent: e.target.value.replace(/\D/g, "") }))} />
              </Field>
            </>
          )}
          <Button onClick={applyFilters}><Search /> جستجو</Button>
          <Button variant="outline" onClick={() => setMore((v) => !v)} aria-expanded={more}>
            <SlidersHorizontal /> فیلترهای بیشتر
          </Button>
        </Toolbar>

        {more && (
          <Toolbar className="mb-3 rounded-xl border border-dashed p-3">
            <Field label="حداقل قیمت" htmlFor="pf-min-price">
              <Input id="pf-min-price" dir="ltr" inputMode="numeric" className="w-32 tabular" value={draft.minPrice} onChange={(e) => setDraft((d) => ({ ...d, minPrice: e.target.value.replace(/\D/g, "") }))} />
            </Field>
            <Field label="حداکثر قیمت" htmlFor="pf-max-price">
              <Input id="pf-max-price" dir="ltr" inputMode="numeric" className="w-32 tabular" value={draft.maxPrice} onChange={(e) => setDraft((d) => ({ ...d, maxPrice: e.target.value.replace(/\D/g, "") }))} />
            </Field>
            <Field label="حداقل متراژ" htmlFor="pf-min-area">
              <Input id="pf-min-area" dir="ltr" inputMode="numeric" className="w-24 tabular" value={draft.minArea} onChange={(e) => setDraft((d) => ({ ...d, minArea: e.target.value.replace(/\D/g, "") }))} />
            </Field>
            <Field label="حداکثر متراژ" htmlFor="pf-max-area">
              <Input id="pf-max-area" dir="ltr" inputMode="numeric" className="w-24 tabular" value={draft.maxArea} onChange={(e) => setDraft((d) => ({ ...d, maxArea: e.target.value.replace(/\D/g, "") }))} />
            </Field>
            <Field label="حداقل اتاق" htmlFor="pf-min-rooms">
              <Input id="pf-min-rooms" dir="ltr" inputMode="numeric" className="w-20 tabular" value={draft.minRooms} onChange={(e) => setDraft((d) => ({ ...d, minRooms: e.target.value.replace(/\D/g, "") }))} />
            </Field>
            <Field label="حداکثر اتاق" htmlFor="pf-max-rooms">
              <Input id="pf-max-rooms" dir="ltr" inputMode="numeric" className="w-20 tabular" value={draft.maxRooms} onChange={(e) => setDraft((d) => ({ ...d, maxRooms: e.target.value.replace(/\D/g, "") }))} />
            </Field>
            <Field label="شماره تماس" htmlFor="pf-has-phone">
              <NativeSelect id="pf-has-phone" value={draft.hasPhone} onChange={(e) => setDraft((d) => ({ ...d, hasPhone: e.target.value }))} className="w-28">
                <option value="">همه</option>
                <option value="1">دارد</option>
                <option value="0">ندارد</option>
              </NativeSelect>
            </Field>
            <Field label="مرتب‌سازی" htmlFor="pf-sort-by">
              <NativeSelect id="pf-sort-by" value={draft.sortBy} onChange={(e) => setDraft((d) => ({ ...d, sortBy: e.target.value }))} className="w-32">
                {SORT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </NativeSelect>
            </Field>
            <Field label="ترتیب" htmlFor="pf-sort-order">
              <NativeSelect id="pf-sort-order" value={draft.sortOrder} onChange={(e) => setDraft((d) => ({ ...d, sortOrder: e.target.value as "asc" | "desc" }))} className="w-24">
                <option value="desc">نزولی</option>
                <option value="asc">صعودی</option>
              </NativeSelect>
            </Field>
          </Toolbar>
        )}

        {list.isLoading ? (
          <ListSkeleton rows={6} />
        ) : list.isError ? (
          <ErrorNote error={list.error} />
        ) : !items.length ? (
          <Empty icon={Building2}>هیچ ملکی یافت نشد</Empty>
        ) : (
          <div className="overflow-x-auto rounded-xl border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>کد ملک</TableHead>
                  <TableHead>عنوان</TableHead>
                  <TableHead>شهر</TableHead>
                  <TableHead>متراژ</TableHead>
                  <TableHead>اتاق</TableHead>
                  <TableHead>قیمت</TableHead>
                  <TableHead>شماره تماس</TableHead>
                  <TableHead>عملیات</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((p) => {
                  const url = safeUrl(p.url);
                  return (
                    <TableRow key={p.id}>
                      <TableCell className="font-mono text-xs tabular text-primary">{formatSerial(p.serial_no)}</TableCell>
                      <TableCell className="max-w-64">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="truncate" title={p.title}>{truncate(p.title, 40)}</span>
                          <AgencyBadge p={p} />
                          <DupBadge of={p.ai_duplicate_of} onOpen={setOpenId} />
                        </div>
                      </TableCell>
                      <TableCell>{p.city_name || "—"}</TableCell>
                      <TableCell className="tabular">{p.area !== null && p.area !== undefined ? `${faNum(p.area)} متر` : "—"}</TableCell>
                      <TableCell className="tabular">{p.rooms !== null && p.rooms !== undefined ? faNum(p.rooms) : "—"}</TableCell>
                      <TableCell className="tabular">
                        {p.listing_type === "rent" ? (
                          <div className="text-xs leading-5">
                            <div>رهن: {price(p.deposit)}</div>
                            <div>اجاره: {price(p.rent_price)}</div>
                          </div>
                        ) : price(p.total_price || p.price)}
                      </TableCell>
                      <TableCell className="tabular">
                        {p.phone_number ? <PhoneLink phone={p.phone_number} /> : <NoPhoneCell p={p} />}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1">
                          <Button variant="ghost" size="icon-sm" aria-label="مشاهدهٔ جزئیات" onClick={() => setOpenId(p.id)}><Eye className="size-4" /></Button>
                          {url && (
                            <Button asChild variant="ghost" size="icon-sm" aria-label="مشاهده در دیوار">
                              <a href={url} target="_blank" rel="noopener noreferrer"><ExternalLink className="size-4" /></a>
                            </Button>
                          )}
                          <Button variant="ghost" size="icon-sm" aria-label="حذف" onClick={() => handleDelete(p.id, p.title)}><Trash2 className="size-4 text-destructive" /></Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}

        <div className="mt-4">
          <Pagination page={page} pages={pages} onPage={setPage} />
        </div>
      </Section>

      <PropertySheet propertyId={openId} onClose={() => setOpenId(null)} onOpenMatch={setMatchId} onDeleted={() => setOpenId(null)} />
      <MatchDialog propertyId={matchId} onClose={() => setMatchId(null)} onOpenProperty={(id) => { setMatchId(null); setOpenId(id); }} />
    </div>
  );
}
