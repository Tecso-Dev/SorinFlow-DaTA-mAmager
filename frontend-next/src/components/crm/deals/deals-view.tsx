"use client";

// «معاملات»: deals list, status filter, create/edit with a buyer/seller
// contact picker, and JSON export (superadmin only).

import { FileJson, FileSpreadsheet, Handshake, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Empty, ErrorNote, LabelBadge, ListSkeleton, NativeSelect, PageHeader, Section, Toolbar, useConfirm } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { DEAL_STATUS, DEAL_TYPE, exportHref, price, qs } from "@/lib/crm";
import { faDate, faNum } from "@/lib/format";
import { can, useSession } from "@/lib/session";
import { AnimatedRow } from "../customers/customers-view";
import { DealDialog } from "./deal-dialog";

type DealRow = {
  id: number;
  title: string;
  deal_type: string | null;
  status: string | null;
  amount: number | null;
  buyer_name: string | null;
  seller_name: string | null;
  contract_date: string | null;
  created_at: string | null;
};

export function DealsView() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const user = useSession().data?.user;
  const isSuperAdmin = can(user, { roles: ["root", "super_admin"] });

  const [status, setStatus] = useState("");
  const [dealType, setDealType] = useState("");
  const [dialogId, setDialogId] = useState<number | null | undefined>(undefined);

  const filters = { status, deal_type: dealType };
  const query = useQuery({
    queryKey: ["crm", "deals", filters],
    queryFn: () => api<{ items: DealRow[]; total: number }>(`/crm/deals${qs({ ...filters, limit: 200 })}`),
  });

  async function remove(d: DealRow) {
    if (!(await confirm({ title: "حذف معامله", description: `«${d.title}» حذف شود؟ این کار برگشت‌پذیر نیست.`, danger: true, icon: Trash2 }))) return;
    try {
      await api(`/crm/deals/${d.id}`, { method: "DELETE" });
      toast.success("معامله حذف شد");
      qc.invalidateQueries({ queryKey: ["crm", "deals"] });
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "حذف ناموفق بود");
    }
  }

  return (
    <div className="grid gap-5">
      <PageHeader
        icon={Handshake}
        title="معاملات"
        hint="خرید، رهن و اجاره — از مذاکره تا بستن قرارداد"
        actions={
          <>
            <Button asChild variant="outline" size="sm" className="gap-1.5">
              <a href={exportHref("/crm/deals/export/excel", filters)} download>
                <FileSpreadsheet className="size-4" /> خروجی اکسل
              </a>
            </Button>
            {isSuperAdmin && (
              <Button asChild variant="outline" size="sm" className="gap-1.5">
                <a href={exportHref("/crm/deals/export/json", filters)} download>
                  <FileJson className="size-4" /> خروجی JSON
                </a>
              </Button>
            )}
            <Button size="sm" className="gap-1.5" onClick={() => setDialogId(null)}>
              <Plus className="size-4" /> معاملهٔ جدید
            </Button>
          </>
        }
      />

      <Section>
        <Toolbar className="mb-4">
          <NativeSelect value={status} onChange={(e) => setStatus(e.target.value)} className="w-36" aria-label="وضعیت">
            <option value="">همهٔ وضعیت‌ها</option>
            {Object.entries(DEAL_STATUS).map(([v, l]) => <option key={v} value={v}>{l.label}</option>)}
          </NativeSelect>
          <NativeSelect value={dealType} onChange={(e) => setDealType(e.target.value)} className="w-32" aria-label="نوع">
            <option value="">همهٔ انواع</option>
            <option value="buy">{DEAL_TYPE.buy}</option>
            <option value="rent">{DEAL_TYPE.rent}</option>
            <option value="lease">{DEAL_TYPE.lease}</option>
          </NativeSelect>
          {query.data && <span className="ms-auto self-center text-xs text-muted-foreground">{faNum(query.data.total)} معامله</span>}
        </Toolbar>

        {query.isLoading && <ListSkeleton rows={6} />}
        {query.isError && <ErrorNote error={query.error} />}
        {query.data && query.data.items.length === 0 && (
          <Empty icon={Handshake} action={<Button size="sm" onClick={() => setDialogId(null)}>ثبت اولین معامله</Button>}>
            هیچ معامله‌ای با این فیلترها ثبت نشده.
          </Empty>
        )}
        {query.data && query.data.items.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>عنوان</TableHead>
                <TableHead>نوع</TableHead>
                <TableHead>وضعیت</TableHead>
                <TableHead>مبلغ</TableHead>
                <TableHead>خریدار</TableHead>
                <TableHead>فروشنده</TableHead>
                <TableHead>تاریخ</TableHead>
                <TableHead className="text-end">عملیات</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {query.data.items.map((d, i) => (
                <AnimatedRow key={d.id} delay={Math.min(i, 8) * 0.02}>
                  <TableCell className="max-w-52 truncate font-semibold">{d.title}</TableCell>
                  <TableCell className="text-muted-foreground">{d.deal_type ? DEAL_TYPE[d.deal_type] ?? d.deal_type : "—"}</TableCell>
                  <TableCell><LabelBadge map={DEAL_STATUS} value={d.status} /></TableCell>
                  <TableCell className="tabular">{price(d.amount)}</TableCell>
                  <TableCell className="max-w-32 truncate text-muted-foreground">{d.buyer_name || "—"}</TableCell>
                  <TableCell className="max-w-32 truncate text-muted-foreground">{d.seller_name || "—"}</TableCell>
                  <TableCell className="tabular text-muted-foreground">
                    {d.contract_date ? faDate(new Date(d.contract_date), { day: "numeric", month: "short", year: "numeric" }) : "—"}
                  </TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-1">
                      <Button variant="ghost" size="icon-sm" aria-label="ویرایش" title="ویرایش" onClick={() => setDialogId(d.id)}>
                        <Pencil className="size-4" />
                      </Button>
                      <Button variant="ghost" size="icon-sm" aria-label="حذف" title="حذف" className="text-destructive hover:bg-destructive/10" onClick={() => remove(d)}>
                        <Trash2 className="size-4" />
                      </Button>
                    </div>
                  </TableCell>
                </AnimatedRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Section>

      <DealDialog
        open={dialogId !== undefined}
        onOpenChange={(o) => !o && setDialogId(undefined)}
        dealId={dialogId ?? null}
        onSaved={() => qc.invalidateQueries({ queryKey: ["crm", "deals"] })}
      />
    </div>
  );
}
