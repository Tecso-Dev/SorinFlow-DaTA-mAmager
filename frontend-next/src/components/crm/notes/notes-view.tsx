"use client";

// «یادداشت‌ها»: a plain list of sticky notes, newest first, with create,
// edit (new — the old panel only had create/delete) and delete.

import { NotebookPen, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Empty, ErrorNote, ListSkeleton, PageHeader, Section, Toolbar, useConfirm } from "@/components/panel/kit";
import { Reveal } from "@/components/viz";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { faDate, faNum } from "@/lib/format";
import { NoteDialog, type NoteRecord } from "./note-dialog";

export function NotesView() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [filterText, setFilterText] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<NoteRecord | null>(null);

  const query = useQuery({
    queryKey: ["crm", "notes"],
    queryFn: () => api<{ items: NoteRecord[]; total: number }>("/crm/notes?limit=100"),
  });

  const items = (query.data?.items ?? []).filter((n) => n.content.toLowerCase().includes(filterText.trim().toLowerCase()));

  async function remove(n: NoteRecord) {
    if (!(await confirm({ title: "حذف یادداشت", description: "این یادداشت حذف شود؟", danger: true, icon: Trash2 }))) return;
    try {
      await api(`/crm/notes/${n.id}`, { method: "DELETE" });
      toast.success("یادداشت حذف شد");
      qc.invalidateQueries({ queryKey: ["crm", "notes"] });
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "حذف ناموفق بود");
    }
  }

  function openNew() {
    setEditing(null);
    setDialogOpen(true);
  }
  function openEdit(n: NoteRecord) {
    setEditing(n);
    setDialogOpen(true);
  }

  return (
    <div className="grid gap-5">
      <PageHeader
        icon={NotebookPen}
        title="یادداشت‌ها"
        hint="یادداشت‌های آزاد، قابل اتصال به مخاطب، معامله یا ملک"
        actions={
          <Button size="sm" className="gap-1.5" onClick={openNew}>
            <Plus className="size-4" /> یادداشت جدید
          </Button>
        }
      />

      <Section>
        <Toolbar className="mb-4">
          <Input value={filterText} onChange={(e) => setFilterText(e.target.value)} placeholder="جستجو در متن یادداشت‌ها…" className="w-64" />
          {query.data && <span className="ms-auto self-center text-xs text-muted-foreground">{faNum(items.length)} از {faNum(query.data.total)} یادداشت</span>}
        </Toolbar>

        {query.isLoading && <ListSkeleton rows={5} />}
        {query.isError && <ErrorNote error={query.error} />}
        {query.data && items.length === 0 && (
          <Empty icon={NotebookPen} action={<Button size="sm" onClick={openNew}>ثبت اولین یادداشت</Button>}>
            یادداشتی یافت نشد.
          </Empty>
        )}
        <div className="grid gap-2.5 sm:grid-cols-2">
          {items.map((n, i) => (
            <Reveal key={n.id} delay={Math.min(i, 8) * 0.03}>
              <div className="flex h-full flex-col gap-2 rounded-xl border-s-2 border-s-primary bg-muted/30 p-3.5">
                <p className="flex-1 whitespace-pre-wrap text-sm leading-6">{n.content}</p>
                <div className="flex items-center justify-between gap-2 border-t pt-2 text-xs text-muted-foreground">
                  <span>
                    {n.created_at ? faDate(new Date(n.created_at), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : ""}
                    {n.created_by ? ` — ${n.created_by}` : ""}
                  </span>
                  <div className="flex gap-1">
                    <Button variant="ghost" size="icon-xs" aria-label="ویرایش" title="ویرایش" onClick={() => openEdit(n)}>
                      <Pencil className="size-3.5" />
                    </Button>
                    <Button variant="ghost" size="icon-xs" aria-label="حذف" title="حذف" className="text-destructive hover:bg-destructive/10" onClick={() => remove(n)}>
                      <Trash2 className="size-3.5" />
                    </Button>
                  </div>
                </div>
              </div>
            </Reveal>
          ))}
        </div>
      </Section>

      <NoteDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        note={editing}
        onSaved={() => qc.invalidateQueries({ queryKey: ["crm", "notes"] })}
      />
    </div>
  );
}
