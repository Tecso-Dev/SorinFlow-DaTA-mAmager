"use client";

// «مشتریان»: the intake list (search / temperature / source / sort), the
// create-edit dialog and the suggested-listings sheet.

import { FileSpreadsheet, Pencil, Plus, Sparkles, Trash2, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Empty, ErrorNote, LabelBadge, ListSkeleton, NativeSelect, PageHeader, Pagination, Section, ToneBadge, Toolbar, useConfirm } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { CUSTOMER_SOURCE, TEMPERATURE, exportHref, price, qs } from "@/lib/crm";
import { faNum } from "@/lib/format";
import { CustomerDialog } from "./customer-dialog";
import { CustomerMatchesSheet } from "./matches-sheet";

type CustomerRow = {
  id: number;
  full_name: string;
  mobile1: string | null;
  temperature: string | null;
  source: string | null;
  budget_max: number | null;
  desired_district: string | null;
  consultant_name: string | null;
  followups: { date: string; time: string }[];
  created_at: string | null;
};

const PAGE_SIZE = 20;

/** A table row that fades and lifts in — `Reveal` (viz.tsx) renders a plain
 *  `<div>`, which HTML forbids as a direct child of `<tbody>`, so table rows
 *  get their own `motion.tr` instead. */
export function AnimatedRow({ delay = 0, className, children }: { delay?: number; className?: string; children: React.ReactNode }) {
  return (
    <motion.tr
      initial={{ opacity: 0, y: 10 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "0px 0px -20px 0px" }}
      transition={{ duration: 0.4, delay, ease: [0.22, 1, 0.36, 1] }}
      className={cn(
        "border-b transition-colors hover:bg-muted/50 has-aria-expanded:bg-muted/50 data-[state=selected]:bg-muted",
        className,
      )}
    >
      {children}
    </motion.tr>
  );
}

export function CustomersView() {
  const qc = useQueryClient();
  const confirm = useConfirm();

  const [searchText, setSearchText] = useState("");
  const [search, setSearch] = useState("");
  const [temperature, setTemperature] = useState("");
  const [source, setSource] = useState("");
  const [sort, setSort] = useState("newest");
  const [page, setPage] = useState(1);

  const [dialogId, setDialogId] = useState<number | null | undefined>(undefined);
  const [matchesFor, setMatchesFor] = useState<{ id: number; name: string } | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setSearch(searchText.trim()), 300);
    return () => clearTimeout(t);
  }, [searchText]);

  const filters = { search, temperature, source, sort };
  // a filter change starts back at page 1 — adjusted during render (the
  // React-recommended way), not in an effect that would render twice
  const filterKey = JSON.stringify(filters);
  const [lastFilterKey, setLastFilterKey] = useState(filterKey);
  if (filterKey !== lastFilterKey) {
    setLastFilterKey(filterKey);
    setPage(1);
  }

  const query = useQuery({
    queryKey: ["crm", "customers", filters, page],
    queryFn: () =>
      api<{ items: CustomerRow[]; total: number }>(
        `/crm/customers${qs({ ...filters, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE })}`,
      ),
  });

  async function remove(c: CustomerRow) {
    if (!(await confirm({ title: "حذف مشتری", description: `«${c.full_name}» حذف شود؟ این کار برگشت‌پذیر نیست.`, danger: true, icon: Trash2 }))) return;
    try {
      await api(`/crm/customers/${c.id}`, { method: "DELETE" });
      toast.success("مشتری حذف شد");
      qc.invalidateQueries({ queryKey: ["crm", "customers"] });
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "حذف ناموفق بود");
    }
  }

  // computed once (a lazy initializer, not a render-time Date.now() call) —
  // near enough for a "new" badge that need not be to-the-second accurate
  const [newSince] = useState(() => Date.now() - 3 * 86_400_000);
  const isNewRow = (iso: string | null) => !!iso && new Date(iso).getTime() > newSince;
  const nextFollowup = (c: CustomerRow) => {
    const f = c.followups?.[0];
    if (!f) return "—";
    return [f.date, f.time].filter(Boolean).join(" ") || "—";
  };

  return (
    <div className="grid gap-5">
      <PageHeader
        icon={Users}
        title="مشتریان"
        hint="فرم پروفایل مشتری، بودجه و درخواست، خط تولید بازدید و پیگیری"
        actions={
          <>
            <Button asChild variant="outline" size="sm" className="gap-1.5">
              <a href={exportHref("/crm/customers/export/excel", filters)} download>
                <FileSpreadsheet className="size-4" /> خروجی اکسل
              </a>
            </Button>
            <Button size="sm" className="gap-1.5" onClick={() => setDialogId(null)}>
              <Plus className="size-4" /> مشتری جدید
            </Button>
          </>
        }
      />

      <Section>
        <Toolbar className="mb-4">
          <Input value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder="جستجوی نام، موبایل، منطقه، مشاور…" className="w-56" />
          <NativeSelect value={temperature} onChange={(e) => setTemperature(e.target.value)} className="w-32" aria-label="حرارت">
            <option value="">همهٔ حرارت‌ها</option>
            {Object.entries(TEMPERATURE).map(([v, l]) => <option key={v} value={v}>{l.label}</option>)}
          </NativeSelect>
          <NativeSelect value={source} onChange={(e) => setSource(e.target.value)} className="w-32" aria-label="منبع">
            <option value="">همهٔ منبع‌ها</option>
            {Object.entries(CUSTOMER_SOURCE).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </NativeSelect>
          <NativeSelect value={sort} onChange={(e) => setSort(e.target.value)} className="w-32" aria-label="ترتیب">
            <option value="newest">جدیدترین</option>
            <option value="oldest">قدیمی‌ترین</option>
            <option value="name">نام</option>
          </NativeSelect>
          {query.data && <span className="ms-auto self-center text-xs text-muted-foreground">{faNum(query.data.total)} مشتری</span>}
        </Toolbar>

        {query.isLoading && <ListSkeleton rows={6} />}
        {query.isError && <ErrorNote error={query.error} />}
        {query.data && query.data.items.length === 0 && (
          <Empty icon={Users} action={<Button size="sm" onClick={() => setDialogId(null)}>ثبت اولین مشتری</Button>}>
            هیچ مشتری‌ای با این فیلترها ثبت نشده.
          </Empty>
        )}
        {query.data && query.data.items.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>نام</TableHead>
                <TableHead>موبایل</TableHead>
                <TableHead>حرارت</TableHead>
                <TableHead>منبع</TableHead>
                <TableHead>سقف بودجه</TableHead>
                <TableHead>منطقهٔ درخواستی</TableHead>
                <TableHead>مشاور</TableHead>
                <TableHead>پیگیری بعدی</TableHead>
                <TableHead className="text-end">عملیات</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {query.data.items.map((c, i) => (
                <AnimatedRow key={c.id} delay={Math.min(i, 8) * 0.02}>
                  <TableCell className="font-semibold">
                    <span className="inline-flex items-center gap-1.5">
                      {c.full_name}
                      {isNewRow(c.created_at) && <ToneBadge tone="success">جدید</ToneBadge>}
                    </span>
                  </TableCell>
                  <TableCell className="tabular" dir="ltr">
                    {c.mobile1 ? <a href={`tel:${c.mobile1}`} className="text-success hover:underline">{c.mobile1}</a> : "—"}
                  </TableCell>
                  <TableCell><LabelBadge map={TEMPERATURE} value={c.temperature} /></TableCell>
                  <TableCell className="text-muted-foreground">{c.source ? CUSTOMER_SOURCE[c.source] ?? c.source : "—"}</TableCell>
                  <TableCell className="tabular">{price(c.budget_max)}</TableCell>
                  <TableCell className="max-w-40 truncate text-muted-foreground">{c.desired_district || "—"}</TableCell>
                  <TableCell className="text-muted-foreground">{c.consultant_name || "—"}</TableCell>
                  <TableCell className="tabular text-muted-foreground">{nextFollowup(c)}</TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-1">
                      <Button variant="ghost" size="icon-sm" aria-label="ملک‌های پیشنهادی" title="ملک‌های پیشنهادی" onClick={() => setMatchesFor({ id: c.id, name: c.full_name })}>
                        <Sparkles className="size-4" />
                      </Button>
                      <Button variant="ghost" size="icon-sm" aria-label="ویرایش" title="ویرایش" onClick={() => setDialogId(c.id)}>
                        <Pencil className="size-4" />
                      </Button>
                      <Button variant="ghost" size="icon-sm" aria-label="حذف" title="حذف" className="text-destructive hover:bg-destructive/10" onClick={() => remove(c)}>
                        <Trash2 className="size-4" />
                      </Button>
                    </div>
                  </TableCell>
                </AnimatedRow>
              ))}
            </TableBody>
          </Table>
        )}
        {query.data && query.data.total > PAGE_SIZE && (
          <div className="mt-4">
            <Pagination page={page} pages={Math.ceil(query.data.total / PAGE_SIZE)} onPage={setPage} />
          </div>
        )}
      </Section>

      <CustomerDialog
        open={dialogId !== undefined}
        onOpenChange={(o) => !o && setDialogId(undefined)}
        customerId={dialogId ?? null}
        onSaved={() => qc.invalidateQueries({ queryKey: ["crm", "customers"] })}
      />
      <CustomerMatchesSheet
        open={!!matchesFor}
        onOpenChange={(o) => !o && setMatchesFor(null)}
        customerId={matchesFor?.id ?? null}
        customerName={matchesFor?.name}
      />
    </div>
  );
}
