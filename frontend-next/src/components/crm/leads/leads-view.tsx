"use client";

// «لیدها»: every lead the scraper (or a person) brought in. Filters live in
// the URL, so a filtered list can be bookmarked and the back button undoes a
// filter; the Excel export takes the very same filters. 25 to a page, a
// table on a desktop and cards on a phone, bulk status/delete, and the
// lead itself opens in a side drawer (?lead=<id>).

import {
  Bell, CalendarRange, Car, CheckSquare, Compass, Download, ExternalLink, FileText, FolderInput, Loader2,
  MoreVertical, MoveUp, Network, Plus, RefreshCw, Search, Sparkles, SquareDashed, Target, Trash2, X,
} from "lucide-react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { cn } from "cn";
import { JalaliDateInput } from "@/components/panel/date-input";
import {
  Empty, ErrorNote, ListSkeleton, NativeSelect, PageHeader, Pagination, Section, useConfirm,
} from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { CountUp } from "@/components/viz";
import { api, ApiError } from "@/lib/api";
import { ADVERTISER, LEAD_STATUS, LEAD_STATUS_ORDER, PROPERTY_KIND, exportHref, price, qs, type Tone } from "@/lib/crm";
import { faNum } from "@/lib/format";
import { isoDay, startOfJMonth } from "@/lib/jalali";
import { can, useSession } from "@/lib/session";
import { AddLeadDialog } from "./add-lead-dialog";
import { BinderDialog } from "./dialogs";
import { useLeadActions } from "./lead-actions";
import { LeadSheet } from "./lead-sheet";
import { MatchDialog, type MatchTarget } from "./match-dialog";
import { AgencyBadge, day, DupBadge, MoneyInput, PhoneLink, SerialBadge } from "./shared";
import type { Lead, LeadPage } from "./types";

const PAGE = 25;

/** URL key → the list route's parameter. */
const PARAM: Record<string, string> = {
  q: "search", status: "status", notified: "notified", category: "category", kind: "property_kind", adv: "advertiser",
  from: "date_from", to: "date_to", pmin: "price_min", pmax: "price_max",
};
const FILTER_KEYS = Object.keys(PARAM);

const TONE_TEXT: Record<Tone, string> = {
  neutral: "text-muted-foreground", info: "text-info", warning: "text-warning", success: "text-success",
  danger: "text-destructive", primary: "text-primary", violet: "text-chart-5",
};
const TONE_BG: Record<Tone, string> = {
  neutral: "bg-muted", info: "bg-info/10", warning: "bg-warning/12", success: "bg-success/10",
  danger: "bg-destructive/10", primary: "bg-primary/10", violet: "bg-chart-5/12",
};
const TONE_DOT: Record<Tone, string> = {
  neutral: "bg-muted-foreground", info: "bg-info", warning: "bg-warning", success: "bg-success",
  danger: "bg-destructive", primary: "bg-primary", violet: "bg-chart-5",
};

const dayDate = (s: string | null) => {
  const m = s?.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
};

function useFilters() {
  const sp = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const get = (k: string) => sp.get(k) ?? "";
  const page = Math.max(1, Number(sp.get("page")) || 1);
  const lead = Number(sp.get("lead")) || null;

  /** Writes `patch` into the URL; a filter change goes back to page 1. */
  function set(patch: Record<string, string | number | null>, opts: { replace?: boolean } = {}) {
    const next = new URLSearchParams(sp.toString());
    for (const [k, v] of Object.entries(patch)) {
      if (v === null || v === "") next.delete(k);
      else next.set(k, String(v));
    }
    if (Object.keys(patch).some((k) => FILTER_KEYS.includes(k))) next.delete("page");
    const s = next.toString();
    const url = `${pathname}${s ? `?${s}` : ""}`;
    if (opts.replace) router.replace(url, { scroll: false });
    else router.push(url, { scroll: false });
  }

  const filters = Object.fromEntries(FILTER_KEYS.map((k) => [k, get(k)])) as Record<string, string>;
  const apiParams = Object.fromEntries(FILTER_KEYS.map((k) => [PARAM[k], filters[k] || null]));
  return { filters, apiParams, page, lead, set };
}

export function LeadsView() {
  const user = useSession().data?.user;
  const qc = useQueryClient();
  const confirm = useConfirm();
  const actions = useLeadActions();
  const { filters, apiParams, page, lead, set } = useFilters();
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [adding, setAdding] = useState(false);
  const [match, setMatch] = useState<MatchTarget | null>(null);
  const [binder, setBinder] = useState<number | null>(null);

  const filterKey = qs(apiParams);
  const list = useQuery({
    queryKey: ["crm", "leads", filterKey, page],
    queryFn: () => api<LeadPage>(`/crm/leads${qs({ ...apiParams, limit: PAGE, offset: (page - 1) * PAGE })}`),
    placeholderData: keepPreviousData,
  });
  const stats = useQuery({
    queryKey: ["crm", "leads", "stats"],
    queryFn: () => api<{ leads: { total: number; by_status: Record<string, number> } }>("/crm/stats"),
    staleTime: 60_000,
  });
  const cats = useQuery({
    queryKey: ["scraper", "categories"],
    queryFn: () => api<{ slug: string; name: string; type: string }[]>("/scraper/categories"),
    staleTime: 10 * 60_000,
  });

  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE));
  const onPage = items.map((l) => l.id);
  const allOn = onPage.length > 0 && onPage.every((id) => selected.has(id));
  const someOn = onPage.some((id) => selected.has(id));
  const filing = can(user, { perm: "filing" });

  function toggle(id: number, on: boolean) {
    setSelected((s) => {
      const n = new Set(s);
      if (on) n.add(id);
      else n.delete(id);
      return n;
    });
  }

  const bulk = useMutation({
    mutationFn: (body: { action: "status" | "delete"; status?: string }) =>
      api<{ updated?: number; deleted?: number }>("/crm/leads/bulk", { json: { ids: [...selected], ...body } }),
    onSuccess: (r) => {
      toast.success(r.deleted !== undefined ? `${faNum(r.deleted)} لید حذف شد` : `${faNum(r.updated ?? 0)} لید به‌روز شد`);
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["crm", "leads"] });
      qc.invalidateQueries({ queryKey: ["crm", "calls"] });
    },
    onError: (e) => toast.error("انجام نشد", e instanceof ApiError ? e.message : undefined),
  });

  async function bulkDelete() {
    const ok = await confirm({
      title: "حذف گروهی",
      description: `${faNum(selected.size)} لید انتخاب‌شده حذف شوند؟ این کار برگشت‌پذیر نیست.`,
      confirm: "حذف",
      danger: true,
      icon: Trash2,
    });
    if (ok) bulk.mutate({ action: "delete" });
  }

  const openLead = (id: number) => set({ lead: id });
  const closeLead = () => set({ lead: null }, { replace: true });

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        icon={Target}
        title="لیدها"
        hint={
          list.data ? (
            <>
              <CountUp value={total} /> لید{Object.values(filters).some(Boolean) ? " با این فیلترها" : ""}
            </>
          ) : (
            "لیدهای اسکرپ‌شده و دستی"
          )
        }
        actions={
          <>
            <Button variant="outline" asChild>
              <a href={exportHref("/crm/leads/export/excel", apiParams)} download>
                <Download /> خروجی اکسل
              </a>
            </Button>
            <Button onClick={() => setAdding(true)} className="shadow-[0_8px_24px_-8px_rgb(99_102_241/0.8)]">
              <Plus /> لید جدید
            </Button>
          </>
        }
      />

      <StatusStrip
        byStatus={stats.data?.leads.by_status}
        total={stats.data?.leads.total}
        active={filters.status}
        onPick={(s) => set({ status: s === filters.status ? null : s })}
      />

      <Filters filters={filters} set={set} categories={cats.data ?? []} onSemantic={(text) => setMatch({ kind: "semantic", text })} />

      <Section
        bodyClassName="p-0 pt-0"
        className="overflow-hidden"
      >
        <AnimatePresence initial={false}>
          {selected.size > 0 && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="overflow-hidden border-b bg-primary/8"
            >
              <div className="flex flex-wrap items-center gap-2 px-4 py-2.5" role="region" aria-label="کارهای گروهی">
                <span className="flex items-center gap-1.5 text-sm font-semibold text-primary">
                  <CheckSquare className="size-4" aria-hidden />
                  {faNum(selected.size)} لید انتخاب شده
                </span>
                <NativeSelect
                  aria-label="تغییر وضعیت گروهی"
                  className="h-8 w-auto min-w-44"
                  value=""
                  disabled={bulk.isPending}
                  onChange={(e) => e.target.value && bulk.mutate({ action: "status", status: e.target.value })}
                >
                  <option value="">تغییر وضعیت گروهی…</option>
                  {LEAD_STATUS_ORDER.map((s) => <option key={s} value={s}>{LEAD_STATUS[s].label}</option>)}
                </NativeSelect>
                <Button size="sm" variant="destructive" onClick={bulkDelete} disabled={bulk.isPending}>
                  <Trash2 /> حذف گروهی
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>لغو انتخاب</Button>
                {bulk.isPending && <Loader2 className="size-4 animate-spin text-primary" aria-hidden />}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {list.isLoading ? (
          <div className="p-5"><ListSkeleton rows={8} /></div>
        ) : list.isError ? (
          <div className="p-5"><ErrorNote error={list.error} /></div>
        ) : !items.length ? (
          <div className="p-5">
            <Empty
              icon={SquareDashed}
              action={Object.values(filters).some(Boolean) ? (
                <Button variant="outline" size="sm" onClick={() => set(Object.fromEntries(FILTER_KEYS.map((k) => [k, null])))}>پاک کردن فیلترها</Button>
              ) : undefined}
            >
              هیچ لیدی با این فیلترها پیدا نشد.
            </Empty>
          </div>
        ) : (
          <div className={cn("transition-opacity", list.isFetching && list.isPlaceholderData && "opacity-60")}>
            {/* desktop: the table */}
            <div className="hidden md:block">
              <Table className="text-[13px]">
                <TableHeader>
                  <TableRow className="bg-muted/40 hover:bg-muted/40">
                    <TableHead className="w-10 ps-4">
                      <Checkbox
                        aria-label="انتخاب همهٔ این صفحه"
                        checked={allOn ? true : someOn ? "indeterminate" : false}
                        onCheckedChange={(v) => onPage.forEach((id) => toggle(id, v === true))}
                      />
                    </TableHead>
                    <TableHead>کد</TableHead>
                    <TableHead>عنوان ملک</TableHead>
                    <TableHead>قیمت</TableHead>
                    <TableHead className="hidden xl:table-cell">مشخصات</TableHead>
                    <TableHead>شماره تماس</TableHead>
                    <TableHead>وضعیت</TableHead>
                    <TableHead>تاریخ</TableHead>
                    <TableHead className="w-20 pe-4"><span className="sr-only">عملیات</span></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((l, i) => (
                    <motion.tr
                      key={l.id}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: Math.min(i, 12) * 0.018 }}
                      data-state={selected.has(l.id) ? "selected" : undefined}
                      className="border-b transition-colors hover:bg-muted/40 data-[state=selected]:bg-primary/6"
                    >
                      <TableCell className="ps-4">
                        <Checkbox aria-label={`انتخاب لید ${l.property_title ?? l.id}`} checked={selected.has(l.id)} onCheckedChange={(v) => toggle(l.id, v === true)} />
                      </TableCell>
                      <TableCell>{l.serial_no !== null ? <SerialBadge serial={l.serial_no} /> : <span className="text-muted-foreground" title="ملک این لید حذف شده است">—</span>}</TableCell>
                      <TableCell className="max-w-[300px]">
                        <button
                          type="button"
                          onClick={() => openLead(l.id)}
                          className="block max-w-full truncate text-start font-semibold outline-none hover:text-primary focus-visible:text-primary focus-visible:underline"
                          title={l.property_title ?? ""}
                        >
                          {l.property_title || "بدون عنوان"}
                        </button>
                        <div className="mt-0.5 flex flex-wrap items-center gap-1 text-[11px] text-muted-foreground">
                          {[l.city_name, l.district, l.area ? `${faNum(l.area)} متر` : ""].filter(Boolean).join(" · ")}
                          <AgencyBadge p={l} />
                          <DupBadge of={l.ai_duplicate_of} />
                        </div>
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        <div className="font-semibold">{price(l.price)}</div>
                        {l.price_per_meter ? <div className="text-[11px] text-muted-foreground">{price(l.price_per_meter)} / متر</div> : null}
                      </TableCell>
                      <TableCell className="hidden xl:table-cell"><SpecChips l={l} /></TableCell>
                      <TableCell className="whitespace-nowrap">
                        {l.phone_number ? <PhoneLink phone={l.phone_number} className="text-[13px]" /> : <NoPhone l={l} />}
                      </TableCell>
                      <TableCell><StatusSelect lead={l} onChange={(s) => actions.setStatus(l.id, s)} /></TableCell>
                      <TableCell className="whitespace-nowrap text-[12px]">
                        <div>{day(l.created_at)}</div>
                        {l.scraped_at && <div className="text-[11px] text-muted-foreground" title="تاریخ برداشت آگهی"><Download className="me-0.5 inline size-3" aria-hidden />{day(l.scraped_at)}</div>}
                        {l.notified && <div className="text-[11px] text-success"><Bell className="me-0.5 inline size-3" aria-hidden />اطلاع داده شد</div>}
                      </TableCell>
                      <TableCell className="pe-4">
                        <RowMenu l={l} filing={filing} onOpen={openLead} onSimilar={() => setMatch({ kind: "lead", id: l.id })} onBinder={() => setBinder(l.property_id)} actions={actions} />
                      </TableCell>
                    </motion.tr>
                  ))}
                </TableBody>
              </Table>
            </div>

            {/* phone: cards */}
            <ul className="grid gap-2 p-3 md:hidden">
              {items.map((l, i) => (
                <motion.li
                  key={l.id}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: Math.min(i, 10) * 0.03 }}
                  className={cn("rounded-xl border bg-background/50 p-3", selected.has(l.id) && "border-primary/40 bg-primary/6")}
                >
                  <div className="flex items-start gap-2.5">
                    <Checkbox className="mt-1" aria-label={`انتخاب لید ${l.property_title ?? l.id}`} checked={selected.has(l.id)} onCheckedChange={(v) => toggle(l.id, v === true)} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <SerialBadge serial={l.serial_no} />
                        <AgencyBadge p={l} />
                        <DupBadge of={l.ai_duplicate_of} />
                      </div>
                      <button type="button" onClick={() => openLead(l.id)} className="mt-1 block w-full text-start text-sm leading-6 font-bold outline-none focus-visible:text-primary">
                        {l.property_title || "بدون عنوان"}
                      </button>
                      <div className="text-[11px] text-muted-foreground">
                        {[l.city_name, l.district, l.area ? `${faNum(l.area)} متر` : "", day(l.created_at)].filter(Boolean).join(" · ")}
                      </div>
                    </div>
                    <RowMenu l={l} filing={filing} onOpen={openLead} onSimilar={() => setMatch({ kind: "lead", id: l.id })} onBinder={() => setBinder(l.property_id)} actions={actions} />
                  </div>
                  <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2">
                    <span className="text-sm font-bold text-primary">{price(l.price)}</span>
                    {l.phone_number ? <PhoneLink phone={l.phone_number} /> : <NoPhone l={l} />}
                  </div>
                  <div className="mt-2 flex items-center justify-between gap-2">
                    <SpecChips l={l} />
                    <StatusSelect lead={l} onChange={(s) => actions.setStatus(l.id, s)} />
                  </div>
                </motion.li>
              ))}
            </ul>

            <div className="flex flex-col items-center gap-2 border-t px-4 py-3 sm:flex-row sm:justify-between">
              <span className="text-xs text-muted-foreground tabular">
                {faNum((page - 1) * PAGE + 1)}–{faNum(Math.min(page * PAGE, total))} از {faNum(total)}
              </span>
              <Pagination page={page} pages={pages} onPage={(p) => set({ page: p === 1 ? null : p })} />
              <Button variant="ghost" size="sm" onClick={() => list.refetch()} disabled={list.isFetching} aria-label="بارگیری دوباره">
                <RefreshCw className={cn(list.isFetching && "animate-spin")} />
              </Button>
            </div>
          </div>
        )}
      </Section>

      <AddLeadDialog open={adding} onOpenChange={setAdding} categories={cats.data ?? []} />
      <LeadSheet id={lead} onClose={closeLead} />
      <MatchDialog target={match} onClose={() => setMatch(null)} />
      <BinderDialog propertyId={binder} onClose={() => setBinder(null)} />
    </div>
  );
}

/* ───────────────────────── pieces ───────────────────────── */

function StatusStrip({ byStatus, total, active, onPick }: { byStatus?: Record<string, number>; total?: number; active: string; onPick: (s: string) => void }) {
  if (!byStatus || !total) return null;
  return (
    <div className="grid gap-2">
      {/* the funnel as one bar: each status its share of all leads */}
      <div className="flex h-2 overflow-hidden rounded-full bg-muted" aria-hidden>
        {LEAD_STATUS_ORDER.map((s) => (
          <motion.div
            key={s}
            className={cn(TONE_DOT[LEAD_STATUS[s].tone], "h-full")}
            initial={{ width: 0 }}
            animate={{ width: `${((byStatus[s] ?? 0) / total) * 100}%` }}
            transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
          />
        ))}
      </div>
      <div className="-mx-4 flex gap-1.5 overflow-x-auto px-4 pb-1 [scrollbar-width:none] sm:mx-0 sm:flex-wrap sm:px-0" role="group" aria-label="فیلتر سریع وضعیت">
        {LEAD_STATUS_ORDER.map((s) => {
          const l = LEAD_STATUS[s];
          const on = active === s;
          return (
            <button
              key={s}
              type="button"
              aria-pressed={on}
              onClick={() => onPick(s)}
              className={cn(
                "flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium outline-none transition",
                "hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring",
                on ? cn(TONE_BG[l.tone], TONE_TEXT[l.tone], "border-current") : "text-muted-foreground",
              )}
            >
              <span className={cn("size-2 rounded-full", TONE_DOT[l.tone])} aria-hidden />
              {l.label}
              <span className="tabular opacity-80">{faNum(byStatus[s] ?? 0)}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function Filters({
  filters, set, categories, onSemantic,
}: {
  filters: Record<string, string>;
  set: (p: Record<string, string | number | null>, o?: { replace?: boolean }) => void;
  categories: { name: string; type: string }[];
  onSemantic: (text: string) => void;
}) {
  // typed filters wait for a pause before they touch the URL
  const [q, setQ] = useState(filters.q);
  const [pmin, setPmin] = useState<number | null>(filters.pmin ? Number(filters.pmin) : null);
  const [pmax, setPmax] = useState<number | null>(filters.pmax ? Number(filters.pmax) : null);
  const [seen, setSeen] = useState({ q: filters.q, pmin: filters.pmin, pmax: filters.pmax });
  const [ai, setAi] = useState("");
  // the URL moved on its own (back, a chip, «پاک کردن»): follow it
  if (seen.q !== filters.q || seen.pmin !== filters.pmin || seen.pmax !== filters.pmax) {
    setSeen({ q: filters.q, pmin: filters.pmin, pmax: filters.pmax });
    setQ(filters.q);
    setPmin(filters.pmin ? Number(filters.pmin) : null);
    setPmax(filters.pmax ? Number(filters.pmax) : null);
  }
  useEffect(() => {
    const next = { q: q.trim(), pmin: pmin ? String(pmin) : "", pmax: pmax ? String(pmax) : "" };
    if (next.q === filters.q && next.pmin === filters.pmin && next.pmax === filters.pmax) return;
    const t = setTimeout(() => set(next, { replace: true }), 450);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, pmin, pmax]);

  const from = dayDate(filters.from);
  const to = dayDate(filters.to);
  function preset(p: "today" | "week" | "month" | "last30") {
    const now = new Date();
    const f = new Date(now);
    if (p === "week") f.setDate(now.getDate() - 6);
    else if (p === "month") f.setTime(startOfJMonth(now).getTime());
    else if (p === "last30") f.setDate(now.getDate() - 30);
    set({ from: isoDay(f), to: isoDay(now) });
  }
  const presetOn = (p: string) => {
    if (!filters.from || !filters.to) return false;
    const now = new Date();
    const f = new Date(now);
    if (p === "week") f.setDate(now.getDate() - 6);
    else if (p === "month") f.setTime(startOfJMonth(now).getTime());
    else if (p === "last30") f.setDate(now.getDate() - 30);
    return filters.from === isoDay(f) && filters.to === isoDay(now);
  };

  const chips: [string, string][] = [];
  if (filters.q) chips.push(["q", `جستجو: ${filters.q}`]);
  if (filters.category) chips.push(["category", filters.category]);
  if (filters.kind) chips.push(["kind", PROPERTY_KIND[filters.kind] ?? filters.kind]);
  if (filters.adv) chips.push(["adv", filters.adv === "agency" ? "املاکی" : ADVERTISER[filters.adv] ?? filters.adv]);
  if (filters.status) chips.push(["status", LEAD_STATUS[filters.status]?.label ?? filters.status]);
  if (filters.notified) chips.push(["notified", filters.notified === "true" ? "اطلاع داده شده" : "بدون اطلاع"]);
  if (filters.from || filters.to) chips.push(["date", `${from ? day(from.toISOString()) : "…"} تا ${to ? day(to.toISOString()) : "…"}`]);
  if (filters.pmin || filters.pmax) chips.push(["price", `${filters.pmin ? price(Number(filters.pmin)) : "…"} تا ${filters.pmax ? price(Number(filters.pmax)) : "…"}`]);

  const clearKey = (k: string) =>
    k === "date" ? set({ from: null, to: null }) : k === "price" ? set({ pmin: null, pmax: null }) : set({ [k]: null });

  return (
    <Section bodyClassName="grid gap-3 p-4">
      <div className="grid grid-cols-1 gap-2 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <div className="relative">
          <Search className="pointer-events-none absolute start-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            type="search"
            aria-label="جستجو در لیدها"
            placeholder="جستجو: خیابان، عنوان، شماره، کد ملک…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && set({ q: q.trim() || null }, { replace: true })}
            className="h-9 ps-9"
          />
        </div>
        <form
          className="relative flex gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            if (ai.trim().length < 2) {
              toast.info("جستجوی معنایی", "چند کلمه دربارهٔ آنچه مشتری می‌خواهد بنویسید");
              return;
            }
            onSemantic(ai.trim());
          }}
        >
          <Sparkles className="pointer-events-none absolute start-3 top-1/2 size-4 -translate-y-1/2 text-primary" aria-hidden />
          <Input
            aria-label="جستجوی معنایی"
            placeholder="به زبان خودتان: خانهٔ حیاط‌دار برای کافه…"
            value={ai}
            onChange={(e) => setAi(e.target.value)}
            className="h-9 border-primary/30 ps-9"
          />
          <Button type="submit" variant="outline" className="h-9 shrink-0">جستجوی معنایی</Button>
        </form>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        <NativeSelect aria-label="دسته‌بندی" value={filters.category} onChange={(e) => set({ category: e.target.value })}>
          <option value="">همهٔ دسته‌بندی‌ها</option>
          {categories.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
        </NativeSelect>
        <NativeSelect aria-label="نوع ملک" value={filters.kind} onChange={(e) => set({ kind: e.target.value })}>
          <option value="">همهٔ انواع ملک</option>
          {Object.entries(PROPERTY_KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </NativeSelect>
        <NativeSelect aria-label="آگهی‌دهنده" value={filters.adv} onChange={(e) => set({ adv: e.target.value })}>
          <option value="">همهٔ آگهی‌دهنده‌ها</option>
          <option value="personal">شخصی</option>
          <option value="agency">املاکی</option>
        </NativeSelect>
        <NativeSelect aria-label="وضعیت" value={filters.status} onChange={(e) => set({ status: e.target.value })}>
          <option value="">همهٔ وضعیت‌ها</option>
          {LEAD_STATUS_ORDER.map((s) => <option key={s} value={s}>{LEAD_STATUS[s].label}</option>)}
        </NativeSelect>
        <NativeSelect aria-label="اطلاع‌رسانی" value={filters.notified} onChange={(e) => set({ notified: e.target.value })} className="col-span-2 sm:col-span-1">
          <option value="">همهٔ اطلاع‌رسانی‌ها</option>
          <option value="false">بدون اطلاع</option>
          <option value="true">اطلاع داده شده</option>
        </NativeSelect>
      </div>

      <div className="grid grid-cols-1 gap-3 border-t pt-3 xl:grid-cols-2">
        <fieldset className="grid gap-2">
          <legend className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
            <CalendarRange className="size-3.5" aria-hidden /> بازهٔ تاریخ ثبت
          </legend>
          <div className="flex flex-wrap gap-1" role="group" aria-label="بازه‌های آماده">
            {([["today", "امروز"], ["week", "این هفته"], ["month", "این ماه"], ["last30", "۳۰ روز اخیر"]] as const).map(([k, label]) => (
              <Button key={k} type="button" size="xs" variant={presetOn(k) ? "default" : "outline"} aria-pressed={presetOn(k)} onClick={() => preset(k)}>
                {label}
              </Button>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <JalaliDateInput aria-label="از تاریخ" placeholder="از تاریخ" value={from} onChange={(d) => set({ from: d ? isoDay(d) : null })} />
            <JalaliDateInput aria-label="تا تاریخ" placeholder="تا تاریخ" value={to} onChange={(d) => set({ to: d ? isoDay(d) : null })} />
          </div>
        </fieldset>
        <fieldset className="grid gap-2">
          <legend className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
            <FileText className="size-3.5" aria-hidden /> بازهٔ قیمت (تومان) — برای اجاره، ودیعه سنجیده می‌شود
          </legend>
          <div className="grid grid-cols-2 gap-2">
            <MoneyInput aria-label="قیمت از" placeholder="از" value={pmin} onChange={setPmin} />
            <MoneyInput aria-label="قیمت تا" placeholder="تا" value={pmax} onChange={setPmax} />
          </div>
        </fieldset>
      </div>

      <AnimatePresence initial={false}>
        {chips.length > 0 && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="flex flex-wrap items-center gap-1.5 overflow-hidden"
          >
            {chips.map(([k, label]) => (
              <button
                key={k}
                type="button"
                onClick={() => clearKey(k)}
                className="flex items-center gap-1 rounded-full bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary outline-none hover:bg-primary/15 focus-visible:ring-2 focus-visible:ring-ring"
                aria-label={`برداشتن فیلتر ${label}`}
              >
                {label}
                <X className="size-3" aria-hidden />
              </button>
            ))}
            <Button
              size="xs"
              variant="ghost"
              onClick={() => set(Object.fromEntries(FILTER_KEYS.map((k) => [k, null])))}
            >
              پاک کردن همه
            </Button>
          </motion.div>
        )}
      </AnimatePresence>
    </Section>
  );
}

function StatusSelect({ lead, onChange }: { lead: Lead; onChange: (s: string) => void }) {
  const tone = LEAD_STATUS[lead.status]?.tone ?? "neutral";
  return (
    <NativeSelect
      aria-label="تغییر وضعیت لید"
      value={lead.status}
      onChange={(e) => onChange(e.target.value)}
      className={cn("h-7 w-auto max-w-40 rounded-full border-transparent px-2.5 text-xs font-semibold", TONE_BG[tone], TONE_TEXT[tone])}
    >
      {LEAD_STATUS_ORDER.map((s) => <option key={s} value={s}>{LEAD_STATUS[s].label}</option>)}
      {!LEAD_STATUS[lead.status] && <option value={lead.status}>{lead.status}</option>}
    </NativeSelect>
  );
}

/** سند / پارکینگ / آسانسور / جهت / نبش as small chips. */
function SpecChips({ l }: { l: Lead }) {
  const chips: { key: string; icon: React.ComponentType<{ className?: string }>; label?: string; title: string; off?: boolean }[] = [];
  if (l.document_type) chips.push({ key: "doc", icon: FileText, label: l.document_type, title: `سند: ${l.document_type}` });
  if (l.has_parking !== null && l.has_parking !== undefined) chips.push({ key: "park", icon: Car, title: `پارکینگ: ${l.has_parking ? "دارد" : "ندارد"}`, off: !l.has_parking });
  if (l.has_elevator !== null && l.has_elevator !== undefined) chips.push({ key: "elev", icon: MoveUp, title: `آسانسور: ${l.has_elevator ? "دارد" : "ندارد"}`, off: !l.has_elevator });
  if (l.building_direction) chips.push({ key: "dir", icon: Compass, label: l.building_direction, title: `جهت: ${l.building_direction}` });
  if (l.corner_type) chips.push({ key: "corner", icon: SquareDashed, label: l.corner_type, title: `نبش: ${l.corner_type}` });
  if (!chips.length) return <span className="text-muted-foreground">—</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {chips.map((c) => (
        <span
          key={c.key}
          title={c.title}
          className={cn(
            "inline-flex max-w-24 items-center gap-0.5 truncate rounded-md border px-1.5 py-0.5 text-[11px]",
            c.off ? "text-muted-foreground/60 line-through decoration-1" : "text-foreground/80",
          )}
        >
          <c.icon className="size-3 shrink-0" />
          {c.label ? <span className="truncate">{c.label}</span> : <span className="sr-only">{c.title}</span>}
        </span>
      ))}
    </div>
  );
}

/** Why there is no number: chat only (the poster's choice), not caught this
 *  run (worth a retry), or an old row. */
function NoPhone({ l }: { l: Lead }) {
  if (l.contact_channel === "chat_only") return <span className="text-[11px] text-muted-foreground" title="آگهی‌دهنده فقط از چت دیوار پاسخ می‌دهد">فقط چت</span>;
  if (l.contact_channel === "unavailable") return <span className="text-[11px] text-warning" title="در اجرای بعدی دوباره تلاش می‌شود">گرفته نشد</span>;
  return <span className="text-muted-foreground">—</span>;
}

function RowMenu({
  l, filing, onOpen, onSimilar, onBinder, actions,
}: {
  l: Lead; filing: boolean; onOpen: (id: number) => void; onSimilar: () => void; onBinder: () => void;
  actions: ReturnType<typeof useLeadActions>;
}) {
  return (
    <div className="flex items-center justify-end gap-0.5">
      <Button variant="ghost" size="icon-sm" className="hidden md:inline-flex" aria-label="باز کردن لید" onClick={() => onOpen(l.id)}>
        <ExternalLink className="-scale-x-100" />
      </Button>
      <DropdownMenu dir="rtl">
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon-sm" aria-label="کارهای بیشتر">
            <MoreVertical />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-52">
          <DropdownMenuItem onSelect={() => onOpen(l.id)}><Target /> جزئیات و ویرایش</DropdownMenuItem>
          {!l.notified && <DropdownMenuItem onSelect={() => actions.notify(l.id)}><Bell /> ارسال اطلاع</DropdownMenuItem>}
          {l.property_url && (
            <DropdownMenuItem asChild>
              <a href={l.property_url} target="_blank" rel="noopener noreferrer"><ExternalLink /> باز کردن آگهی</a>
            </DropdownMenuItem>
          )}
          <DropdownMenuItem onSelect={onSimilar}><Network /> ملک‌های مشابه</DropdownMenuItem>
          {filing && <DropdownMenuItem onSelect={onBinder}><FolderInput /> بایگانی در زونکن</DropdownMenuItem>}
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onSelect={() => actions.remove(l.id)}><Trash2 /> حذف لید</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
