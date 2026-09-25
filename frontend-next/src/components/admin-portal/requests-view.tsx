"use client";

// «درخواست‌های مشتریان»: every visitor property request, for staff with the
// portal permission — the admin-side table documented in
// docs/phase4/inventory-system.md §8, distinct from the visitor-facing pages
// under src/app/portal/.

import { Inbox, RefreshCw, Sparkles } from "lucide-react";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Empty, ErrorNote, ListSkeleton, NativeSelect, PageHeader, Pagination, Section, ToneBadge, Toolbar } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { faDate, faNum } from "@/lib/format";
import { CustomerMatchesSheet } from "@/components/crm/customers/matches-sheet";

type RequestUser = { id: number; full_name: string | null; phone: string | null; email: string | null };

type PortalRequest = {
  id: number;
  deal_type: string;
  property_kind: string | null;
  city: string | null;
  districts: string | null;
  budget_min: number | null;
  budget_max: number | null;
  deposit_max: number | null;
  rent_max: number | null;
  area_min: number | null;
  area_max: number | null;
  status: string;
  admin_note: string | null;
  customer_id: number | null;
  created_at: string;
  user: RequestUser | null;
};

const STATUS_OPTIONS = [
  { value: "new", label: "ثبت شده" },
  { value: "in_review", label: "در حال بررسی" },
  { value: "matched", label: "مورد پیدا شد" },
  { value: "contacted", label: "تماس گرفته شد" },
  { value: "closed", label: "بسته شده" },
];
const DEAL_LABEL: Record<string, string> = { buy: "خرید", rent: "اجاره" };

const PAGE_SIZE = 25;

function budgetText(r: PortalRequest): string {
  const t = (n: number) => `${faNum(n)} ت`;
  if (r.deal_type === "rent") {
    const bits = [];
    if (r.deposit_max) bits.push(`ودیعه تا ${t(r.deposit_max)}`);
    if (r.rent_max) bits.push(`اجاره تا ${t(r.rent_max)}`);
    return bits.join(" / ") || "—";
  }
  if (r.budget_min && r.budget_max) return `${t(r.budget_min)} تا ${t(r.budget_max)}`;
  if (r.budget_max) return `تا ${t(r.budget_max)}`;
  if (r.budget_min) return `از ${t(r.budget_min)}`;
  return "—";
}

export function AdminPortalRequestsView() {
  const qc = useQueryClient();
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [matchesFor, setMatchesFor] = useState<{ id: number; name: string } | null>(null);

  const query = useQuery({
    queryKey: ["portal-admin", "requests", status, page],
    queryFn: () =>
      api<{ items: PortalRequest[]; total: number }>(
        `/portal/admin/requests?${new URLSearchParams({
          ...(status ? { status } : {}),
          limit: String(PAGE_SIZE),
          offset: String((page - 1) * PAGE_SIZE),
        })}`,
      ),
  });

  async function setRowStatus(id: number, next: string) {
    try {
      await api(`/portal/admin/requests/${id}`, { method: "PATCH", json: { status: next } });
      void qc.invalidateQueries({ queryKey: ["portal-admin", "requests"] });
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "تغییر وضعیت ناموفق بود");
    }
  }

  const pages = query.data ? Math.max(1, Math.ceil(query.data.total / PAGE_SIZE)) : 1;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        icon={Inbox}
        title="درخواست‌های مشتریان"
        hint="درخواست‌های ثبت‌شده در پورتال مشتریان، برای بررسی و پیگیری."
        actions={
          <Button variant="outline" size="sm" onClick={() => void query.refetch()} disabled={query.isFetching}>
            <RefreshCw className="size-4" /> به‌روزرسانی
          </Button>
        }
      />

      <Section
        action={
          <Toolbar>
            <NativeSelect
              aria-label="فیلتر وضعیت"
              className="w-44"
              value={status}
              onChange={(e) => {
                setStatus(e.target.value);
                setPage(1);
              }}
            >
              <option value="">همهٔ وضعیت‌ها</option>
              {STATUS_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </NativeSelect>
          </Toolbar>
        }
      >
        {query.isPending ? (
          <ListSkeleton rows={6} />
        ) : query.isError ? (
          <ErrorNote error={query.error} />
        ) : query.data.items.length === 0 ? (
          <Empty icon={Inbox}>درخواستی یافت نشد.</Empty>
        ) : (
          <>
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>مشتری</TableHead>
                    <TableHead>خواسته</TableHead>
                    <TableHead>بودجه</TableHead>
                    <TableHead>وضعیت</TableHead>
                    <TableHead>تاریخ</TableHead>
                    <TableHead>عملیات</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {query.data.items.map((r) => {
                    const need = [DEAL_LABEL[r.deal_type] ?? r.deal_type, r.city, r.districts].filter(Boolean).join(" · ");
                    return (
                      <TableRow key={r.id}>
                        <TableCell>
                          <div className="font-medium">{r.user?.full_name || "—"}</div>
                          <div dir="ltr" className="text-end text-xs text-muted-foreground">
                            {r.user?.phone || r.user?.email || ""}
                          </div>
                        </TableCell>
                        <TableCell className="max-w-56">
                          <div className="truncate">{need || "—"}</div>
                          {r.customer_id && (
                            <div className="mt-0.5 flex items-center gap-1 text-[11px] text-primary">
                              <Sparkles className="size-3" /> در موتور تطبیق
                            </div>
                          )}
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-xs">{budgetText(r)}</TableCell>
                        <TableCell>
                          <NativeSelect
                            aria-label="تغییر وضعیت درخواست"
                            className="w-36"
                            value={r.status}
                            onChange={(e) => void setRowStatus(r.id, e.target.value)}
                          >
                            {STATUS_OPTIONS.map((o) => (
                              <option key={o.value} value={o.value}>
                                {o.label}
                              </option>
                            ))}
                          </NativeSelect>
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                          {faDate(new Date(r.created_at), { day: "numeric", month: "short", year: "numeric" })}
                        </TableCell>
                        <TableCell>
                          {r.customer_id ? (
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => setMatchesFor({ id: r.customer_id as number, name: r.user?.full_name || "مشتری" })}
                            >
                              <Sparkles className="size-3.5" /> ملک‌های مناسب
                            </Button>
                          ) : (
                            <ToneBadge tone="neutral">بدون مشتری</ToneBadge>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
            <div className="mt-4">
              <Pagination page={page} pages={pages} onPage={setPage} />
            </div>
          </>
        )}
      </Section>

      <CustomerMatchesSheet
        open={!!matchesFor}
        onOpenChange={(o) => !o && setMatchesFor(null)}
        customerId={matchesFor?.id ?? null}
        customerName={matchesFor?.name}
      />
    </div>
  );
}
