"use client";

// «دفترچه تلفن»: contacts list, filters, create/edit and JSON export
// (superadmin only — the JSON shape is not gated server-side, so the panel
// hides it from anyone who is not root/super_admin).

import { BookUser, FileJson, FileSpreadsheet, Pencil, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Empty, ErrorNote, LabelBadge, ListSkeleton, NativeSelect, PageHeader, Pagination, Section, Toolbar, useConfirm } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { CONTACT_CATEGORY, CONTACT_TYPE, CONTACT_TYPES, exportHref, qs } from "@/lib/crm";
import { faNum } from "@/lib/format";
import { can, useSession } from "@/lib/session";
import { AnimatedRow } from "../customers/customers-view";
import { ContactDialog } from "./contact-dialog";

type ContactRow = {
  id: number;
  name: string;
  phone: string | null;
  phone2: string | null;
  contact_type: string | null;
  category: string | null;
  city: string | null;
  tags: string | null;
};

const PAGE_SIZE = 20;

export function ContactsView() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const user = useSession().data?.user;
  const isSuperAdmin = can(user, { roles: ["root", "super_admin"] });

  const [searchText, setSearchText] = useState("");
  const [search, setSearch] = useState("");
  const [contactType, setContactType] = useState("");
  const [category, setCategory] = useState("");
  const [page, setPage] = useState(1);
  const [dialogId, setDialogId] = useState<number | null | undefined>(undefined);

  useEffect(() => {
    const t = setTimeout(() => setSearch(searchText.trim()), 300);
    return () => clearTimeout(t);
  }, [searchText]);

  const filters = { search, contact_type: contactType, category };
  const filterKey = JSON.stringify(filters);
  const [lastFilterKey, setLastFilterKey] = useState(filterKey);
  if (filterKey !== lastFilterKey) {
    setLastFilterKey(filterKey);
    setPage(1);
  }

  const query = useQuery({
    queryKey: ["crm", "contacts", filters, page],
    queryFn: () =>
      api<{ items: ContactRow[]; total: number }>(
        `/crm/contacts${qs({ ...filters, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE })}`,
      ),
  });

  async function remove(c: ContactRow) {
    if (!(await confirm({ title: "حذف مخاطب", description: `«${c.name}» حذف شود؟ این کار برگشت‌پذیر نیست.`, danger: true, icon: Trash2 }))) return;
    try {
      await api(`/crm/contacts/${c.id}`, { method: "DELETE" });
      toast.success("مخاطب حذف شد");
      qc.invalidateQueries({ queryKey: ["crm", "contacts"] });
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "حذف ناموفق بود");
    }
  }

  return (
    <div className="grid gap-5">
      <PageHeader
        icon={BookUser}
        title="دفترچه تلفن"
        hint="مالکین، موجرین، مستاجرین، خواهان‌ها، سازندگان و املاک"
        actions={
          <>
            <Button asChild variant="outline" size="sm" className="gap-1.5">
              <a href={exportHref("/crm/contacts/export/excel", filters)} download>
                <FileSpreadsheet className="size-4" /> خروجی اکسل
              </a>
            </Button>
            {isSuperAdmin && (
              <Button asChild variant="outline" size="sm" className="gap-1.5">
                <a href={exportHref("/crm/contacts/export/json", filters)} download>
                  <FileJson className="size-4" /> خروجی JSON
                </a>
              </Button>
            )}
            <Button size="sm" className="gap-1.5" onClick={() => setDialogId(null)}>
              <Plus className="size-4" /> مخاطب جدید
            </Button>
          </>
        }
      />

      <Section>
        <Toolbar className="mb-4">
          <Input value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder="جستجوی نام یا تلفن…" className="w-56" />
          <NativeSelect value={contactType} onChange={(e) => setContactType(e.target.value)} className="w-36" aria-label="دسته">
            <option value="">همهٔ دسته‌ها</option>
            {CONTACT_TYPES.map((v) => <option key={v} value={v}>{CONTACT_TYPE[v]?.label ?? v}</option>)}
          </NativeSelect>
          <NativeSelect value={category} onChange={(e) => setCategory(e.target.value)} className="w-32" aria-label="اولویت">
            <option value="">همه</option>
            {Object.entries(CONTACT_CATEGORY).map(([v, l]) => <option key={v} value={v}>{l.label}</option>)}
          </NativeSelect>
          {query.data && <span className="ms-auto self-center text-xs text-muted-foreground">{faNum(query.data.total)} مخاطب</span>}
        </Toolbar>

        {query.isLoading && <ListSkeleton rows={6} />}
        {query.isError && <ErrorNote error={query.error} />}
        {query.data && query.data.items.length === 0 && (
          <Empty icon={BookUser} action={<Button size="sm" onClick={() => setDialogId(null)}>ثبت اولین مخاطب</Button>}>
            هیچ مخاطبی با این فیلترها ثبت نشده.
          </Empty>
        )}
        {query.data && query.data.items.length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>نام</TableHead>
                <TableHead>تلفن</TableHead>
                <TableHead>دسته</TableHead>
                <TableHead>اولویت</TableHead>
                <TableHead>شهر</TableHead>
                <TableHead>تگ‌ها</TableHead>
                <TableHead className="text-end">عملیات</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {query.data.items.map((c, i) => (
                <AnimatedRow key={c.id} delay={Math.min(i, 8) * 0.02}>
                  <TableCell className="font-semibold">{c.name}</TableCell>
                  <TableCell className="tabular" dir="ltr">
                    {c.phone ? <a href={`tel:${c.phone}`} className="text-success hover:underline">{c.phone}</a> : "—"}
                  </TableCell>
                  <TableCell><LabelBadge map={CONTACT_TYPE} value={c.contact_type} /></TableCell>
                  <TableCell><LabelBadge map={CONTACT_CATEGORY} value={c.category} /></TableCell>
                  <TableCell className="text-muted-foreground">{c.city || "—"}</TableCell>
                  <TableCell className="max-w-40 truncate text-muted-foreground">{c.tags || "—"}</TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-1">
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

      <ContactDialog
        open={dialogId !== undefined}
        onOpenChange={(o) => !o && setDialogId(undefined)}
        contactId={dialogId ?? null}
        onSaved={() => qc.invalidateQueries({ queryKey: ["crm", "contacts"] })}
      />
    </div>
  );
}
