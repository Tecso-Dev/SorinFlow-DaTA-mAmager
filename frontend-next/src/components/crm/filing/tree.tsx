"use client";

// The cabinet → binder → folder tree: GET /filing/cabinets. One level of
// folders only (app/models/crm_models.py Binder docstring). Rename/delete
// live in each row's kebab menu; delete is hidden where the backend would
// refuse it (require_filing_admin — root/super_admin/admin).

import {
  ChevronDown, FolderPlus, FolderTree, Inbox, MoreVertical, Pencil, Plus, Trash2,
} from "lucide-react";
import { useState } from "react";
import { cn } from "cn";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { can, useSession } from "@/lib/session";
import { faNum } from "@/lib/format";
import { CabinetDialog, BinderDialog } from "./cabinet-dialog";
import type { Binder, Cabinet } from "./types";
import type { Scope } from "./filing-page";

export function FilingTree({
  cabinets, scope, onScope, unfiledCount, onChanged,
}: {
  cabinets: Cabinet[];
  scope: Scope;
  onScope: (s: Scope) => void;
  unfiledCount?: number;
  onChanged: () => void;
}) {
  const user = useSession().data?.user;
  const canDelete = can(user, { roles: ["root", "super_admin", "admin"] });
  const confirm = useConfirm();
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());
  const [cabDialog, setCabDialog] = useState<{ open: boolean; cabinet?: Cabinet | null }>({ open: false });
  const [binDialog, setBinDialog] = useState<{ open: boolean; binder?: Binder | null; cabinetId?: number; parentId?: number | null }>({ open: false });

  function toggle(id: number) {
    setCollapsed((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function deleteCabinet(c: Cabinet) {
    if (!(await confirm({ title: `کمد «${c.name}» حذف شود؟`, description: "زونکن‌های داخل آن هم حذف می‌شوند و فایل‌هایشان بدون زونکن می‌مانند.", icon: Trash2, danger: true, confirm: "حذف" }))) return;
    try {
      await api(`/filing/cabinets/${c.id}`, { method: "DELETE" });
      toast.success("کمد حذف شد");
      if (scope.kind === "binder" && cabinets.find((x) => x.id === c.id)?.binders.some((b) => b.id === scope.id)) onScope({ kind: "unfiled" });
      onChanged();
    } catch (e) {
      toast.error("حذف نشد", e instanceof ApiError ? e.message : undefined);
    }
  }

  async function deleteBinder(b: Binder) {
    const isFolder = !!b.parent_id;
    if (!(await confirm({ title: `${isFolder ? "پوشهٔ" : "زونکن"} «${b.name}» حذف شود؟`, description: "فایل‌های داخل آن بدون زونکن می‌مانند.", icon: Trash2, danger: true, confirm: "حذف" }))) return;
    try {
      await api(`/filing/binders/${b.id}`, { method: "DELETE" });
      toast.success("حذف شد");
      if (scope.kind === "binder" && scope.id === b.id) onScope({ kind: "unfiled" });
      onChanged();
    } catch (e) {
      toast.error("حذف نشد", e instanceof ApiError ? e.message : undefined);
    }
  }

  return (
    <nav className="flex flex-col gap-1" aria-label="کمد و زونکن‌ها">
      <button
        type="button"
        onClick={() => onScope({ kind: "unfiled" })}
        className={cn(
          "flex items-center gap-2 rounded-lg px-2.5 py-2 text-start text-sm font-medium outline-none transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring",
          scope.kind === "unfiled" && "bg-primary/12 text-primary",
        )}
      >
        <Inbox className="size-4 shrink-0" />
        <span className="flex-1 truncate">فایل‌های بدون زونکن</span>
        {unfiledCount !== undefined && <span className="text-xs tabular text-muted-foreground">{faNum(unfiledCount)}</span>}
      </button>

      <div className="my-1 flex items-center justify-between px-2.5">
        <span className="text-[11px] font-semibold text-muted-foreground">کمدها</span>
        <Button variant="ghost" size="icon-xs" aria-label="کمد جدید" onClick={() => setCabDialog({ open: true, cabinet: null })}>
          <Plus className="size-3.5" />
        </Button>
      </div>

      {cabinets.map((c) => {
        const open = !collapsed.has(c.id);
        return (
          <div key={c.id}>
            <div
              className={cn(
                "group flex items-center gap-1.5 rounded-lg px-1.5 py-1.5 text-sm hover:bg-accent",
              )}
            >
              <button type="button" onClick={() => toggle(c.id)} className="rounded p-0.5 outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label={open ? "بستن" : "باز کردن"}>
                <ChevronDown className={cn("size-3.5 text-muted-foreground transition-transform", !open && "-rotate-90")} />
              </button>
              <span className="size-2.5 shrink-0 rounded-full" style={{ background: c.color }} />
              <span className="min-w-0 flex-1 truncate font-semibold">{c.name}</span>
              <span className="text-xs tabular text-muted-foreground">{faNum(c.file_count)}</span>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon-xs" className="opacity-100 sm:opacity-0 sm:group-hover:opacity-100" aria-label="عملیات کمد">
                    <MoreVertical className="size-3.5" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start">
                  <DropdownMenuItem onSelect={() => setBinDialog({ open: true, cabinetId: c.id, parentId: null, binder: null })}>
                    <FolderPlus className="size-4" /> زونکن جدید
                  </DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => setCabDialog({ open: true, cabinet: c })}>
                    <Pencil className="size-4" /> ویرایش کمد
                  </DropdownMenuItem>
                  {canDelete && (
                    <DropdownMenuItem variant="destructive" onSelect={() => deleteCabinet(c)}>
                      <Trash2 className="size-4" /> حذف کمد
                    </DropdownMenuItem>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>

            {open && (
              <div className="ms-4 flex flex-col gap-0.5 border-s ps-2">
                {c.binders.map((b) => {
                  const bOpen = !collapsed.has(-b.id - 1000000);
                  const active = scope.kind === "binder" && scope.id === b.id;
                  return (
                    <div key={b.id}>
                      <div className="group flex items-center gap-1">
                        {b.folders?.length ? (
                          <button type="button" onClick={() => toggle(-b.id - 1000000)} className="shrink-0 rounded p-0.5 outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label={bOpen ? "بستن" : "باز کردن"}>
                            <ChevronDown className={cn("size-3 text-muted-foreground transition-transform", !bOpen && "-rotate-90")} />
                          </button>
                        ) : (
                          <span className="w-4 shrink-0" />
                        )}
                        <button
                          type="button"
                          onClick={() => onScope({ kind: "binder", id: b.id })}
                          className={cn(
                            "flex min-w-0 flex-1 items-center gap-1.5 rounded-lg px-1.5 py-1.5 text-start text-[13px] outline-none transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring",
                            active && "bg-primary/12 font-medium text-primary",
                          )}
                        >
                          <span className="size-2 shrink-0 rounded-full" style={{ background: b.color }} />
                          <span className="min-w-0 flex-1 truncate">{b.name}</span>
                          <span className="tabular text-muted-foreground">{faNum(b.file_count)}</span>
                        </button>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon-xs" className="shrink-0 opacity-100 sm:opacity-0 sm:group-hover:opacity-100" aria-label="عملیات زونکن">
                              <MoreVertical className="size-3.5" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="start">
                            <DropdownMenuItem onSelect={() => setBinDialog({ open: true, cabinetId: c.id, parentId: b.id, binder: null })}>
                              <FolderTree className="size-4" /> پوشهٔ جدید
                            </DropdownMenuItem>
                            <DropdownMenuItem onSelect={() => setBinDialog({ open: true, binder: b })}>
                              <Pencil className="size-4" /> ویرایش
                            </DropdownMenuItem>
                            {canDelete && (
                              <DropdownMenuItem variant="destructive" onSelect={() => deleteBinder(b)}>
                                <Trash2 className="size-4" /> حذف
                              </DropdownMenuItem>
                            )}
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                      {bOpen && b.folders && b.folders.length > 0 && (
                        <div className="ms-5 flex flex-col gap-0.5 border-s ps-2">
                          {b.folders.map((f) => {
                            const factive = scope.kind === "binder" && scope.id === f.id;
                            return (
                              <div key={f.id} className="group flex items-center gap-1">
                                <button
                                  type="button"
                                  onClick={() => onScope({ kind: "binder", id: f.id })}
                                  className={cn(
                                    "flex min-w-0 flex-1 items-center gap-1.5 rounded-lg px-1.5 py-1 text-start text-[12px] outline-none transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring",
                                    factive && "bg-primary/12 font-medium text-primary",
                                  )}
                                >
                                  <FolderTree className="size-3 shrink-0 text-muted-foreground" />
                                  <span className="min-w-0 flex-1 truncate">{f.name}</span>
                                  <span className="tabular text-muted-foreground">{faNum(f.file_count)}</span>
                                </button>
                                <DropdownMenu>
                                  <DropdownMenuTrigger asChild>
                                    <Button variant="ghost" size="icon-xs" className="shrink-0 opacity-100 sm:opacity-0 sm:group-hover:opacity-100" aria-label="عملیات پوشه">
                                      <MoreVertical className="size-3" />
                                    </Button>
                                  </DropdownMenuTrigger>
                                  <DropdownMenuContent align="start">
                                    <DropdownMenuItem onSelect={() => setBinDialog({ open: true, binder: f })}>
                                      <Pencil className="size-4" /> ویرایش
                                    </DropdownMenuItem>
                                    {canDelete && (
                                      <DropdownMenuItem variant="destructive" onSelect={() => deleteBinder(f)}>
                                        <Trash2 className="size-4" /> حذف
                                      </DropdownMenuItem>
                                    )}
                                  </DropdownMenuContent>
                                </DropdownMenu>
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                })}
                {!c.binders.length && <p className="px-2 py-1 text-[11px] text-muted-foreground">زونکنی نیست</p>}
              </div>
            )}
          </div>
        );
      })}

      <CabinetDialog open={cabDialog.open} onOpenChange={(o) => setCabDialog((d) => ({ ...d, open: o }))} cabinet={cabDialog.cabinet} onSaved={onChanged} />
      <BinderDialog
        open={binDialog.open}
        onOpenChange={(o) => setBinDialog((d) => ({ ...d, open: o }))}
        binder={binDialog.binder}
        cabinetId={binDialog.cabinetId}
        parentId={binDialog.parentId}
        onSaved={onChanged}
      />
    </nav>
  );
}
