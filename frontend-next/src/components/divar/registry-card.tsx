"use client";

// «همهٔ شماره‌های دیوار و صاحبشان» — root only. The single place in the
// whole panel that changes who owns a Divar number
// (PATCH /auth/registry/{id}/owner). Never wired anywhere else: logging a
// number in, importing its cookie or registering a forwarder never touches
// this column.

import { Info, ShieldCheck, Sparkles, Trash2 } from "lucide-react";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Empty, ErrorNote, ListSkeleton, NativeSelect, Section, ToneBadge, useConfirm } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api, ApiError } from "@/lib/api";
import { faDate, faNum } from "@/lib/format";
import type { RegistryNumber, RegistryResponse } from "./types";

function when(iso: string | null) {
  if (!iso) return null;
  return faDate(new Date(iso), { month: "short", day: "numeric" });
}

const WHY_LABEL = {
  divar_phone: "این کاربر این شماره را شمارهٔ دیوار خود اعلام کرده",
  forwarder: "سیم‌کارت این شماره در گوشی این کاربر است",
} as const;

export function RegistryCard() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [edits, setEdits] = useState<Record<number, string>>({});

  const registry = useQuery({
    queryKey: ["divar", "registry"],
    queryFn: () => api<RegistryResponse>("/auth/registry"),
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["divar", "registry"] });
    qc.invalidateQueries({ queryKey: ["divar", "cookies"] });
  };

  const save = useMutation({
    mutationFn: ({ id, ownerUserId }: { id: number; ownerUserId: number }) =>
      api<{ success: boolean; owner_name: string | null; changed: boolean; moved_jobs: string[] }>(
        `/auth/registry/${id}/owner`,
        { method: "PATCH", json: { owner_user_id: ownerUserId } },
      ),
    onSuccess: (r, vars) => {
      invalidate();
      setEdits((e) => {
        const n = { ...e };
        delete n[vars.id];
        return n;
      });
      if (!r.changed) return;
      toast.success(
        "مالکیت تغییر کرد",
        r.moved_jobs.length ? `${faNum(r.moved_jobs.length)} اسکرپ در حال اجرای صاحب قبلی به شمارهٔ دیگرش منتقل شد` : undefined,
      );
    },
    onError: (e) => toast.error("ذخیره نشد", e instanceof ApiError ? e.message : undefined),
  });

  const del = useMutation({
    mutationFn: (id: number) => api<{ success: boolean }>(`/auth/cookies/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      invalidate();
      toast.success("نشست حذف شد");
    },
    onError: (e) => toast.error("حذف نشد", e instanceof ApiError ? e.message : undefined),
  });

  async function askSave(row: RegistryNumber, ownerUserId: number) {
    const target = registry.data?.users.find((u) => u.id === ownerUserId);
    const ok = await confirm({
      title: "تغییر مالکیت شماره",
      description: (
        <>
          شمارهٔ {row.phone_number} از این پس فقط در اختیار «{target?.name ?? "—"}» است. اگر اسکرپی از صاحب قبلی روی
          این شماره در حال اجراست، به شمارهٔ دیگری از خودش منتقل می‌شود.
        </>
      ),
      confirm: "تغییر مالکیت",
      icon: ShieldCheck,
    });
    if (ok) save.mutate({ id: row.id, ownerUserId });
  }

  async function askDelete(row: RegistryNumber) {
    const ok = await confirm({
      title: "حذف نشست دیوار",
      description: `نشست شمارهٔ ${row.phone_number} برای همیشه حذف شود؟`,
      confirm: "حذف",
      danger: true,
      icon: Trash2,
    });
    if (ok) del.mutate(row.id);
  }

  const rows = registry.data?.numbers ?? [];
  const users = registry.data?.users ?? [];

  return (
    <Section title="همهٔ شماره‌های دیوار و صاحبشان" hint="فقط برای root — تنها جای پنل که مالکیت شماره را عوض می‌کند">
      {registry.isLoading ? (
        <ListSkeleton rows={3} />
      ) : registry.isError ? (
        <ErrorNote error={registry.error} />
      ) : !rows.length ? (
        <Empty icon={ShieldCheck}>هنوز شمارهٔ دیواری ثبت نشده است.</Empty>
      ) : (
        <div className="-mx-5 overflow-x-auto px-5">
          <Table className="text-[13px]">
            <TableHeader>
              <TableRow>
                <TableHead>شماره</TableHead>
                <TableHead>صاحب</TableHead>
                <TableHead>وضعیت</TableHead>
                <TableHead>افشا</TableHead>
                <TableHead className="w-52"><span className="sr-only">عملیات</span></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => {
                const current = edits[row.id] ?? (row.owner_user_id != null ? String(row.owner_user_id) : "");
                const dirty = current !== (row.owner_user_id != null ? String(row.owner_user_id) : "");
                return (
                  <TableRow key={row.id}>
                    <TableCell dir="ltr" className="font-mono">{row.phone_number}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1.5">
                        <NativeSelect
                          aria-label={`صاحب شمارهٔ ${row.phone_number}`}
                          className="h-8 w-40"
                          value={current}
                          onChange={(e) => setEdits((s) => ({ ...s, [row.id]: e.target.value }))}
                        >
                          <option value="">— بی‌صاحب —</option>
                          {users.map((u) => (
                            <option key={u.id} value={u.id}>{u.name}</option>
                          ))}
                        </NativeSelect>
                        {row.suggested_owner && (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                type="button"
                                size="icon-sm"
                                variant="ghost"
                                aria-label={`پیشنهاد: ${row.suggested_owner.name}`}
                                onClick={() => setEdits((s) => ({ ...s, [row.id]: String(row.suggested_owner!.id) }))}
                              >
                                <Sparkles className="text-primary" />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>
                              پیشنهاد: {row.suggested_owner.name} — {WHY_LABEL[row.suggested_owner.why]}
                            </TooltipContent>
                          </Tooltip>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {row.is_valid ? <ToneBadge tone="success">معتبر</ToneBadge> : <ToneBadge tone="danger">نامعتبر</ToneBadge>}
                        {!row.is_enabled && <ToneBadge tone="neutral">خاموش</ToneBadge>}
                        {row.identity_required_at && <ToneBadge tone="warning">احراز هویت لازم</ToneBadge>}
                        {row.in_use && <ToneBadge tone="info">در حال اسکرپ</ToneBadge>}
                      </div>
                      {row.last_checked_at && <div className="mt-0.5 text-[11px] text-muted-foreground">بررسی {when(row.last_checked_at)}</div>}
                    </TableCell>
                    <TableCell className="tabular">{faNum(row.reveals)}</TableCell>
                    <TableCell>
                      <div className="flex items-center justify-end gap-1.5">
                        <Button
                          size="sm"
                          disabled={!dirty || !current || save.isPending}
                          onClick={() => askSave(row, Number(current))}
                        >
                          ذخیره
                        </Button>
                        <Button size="icon-sm" variant="ghost" className="text-destructive hover:text-destructive" aria-label={`حذف نشست ${row.phone_number}`} onClick={() => askDelete(row)}>
                          <Trash2 />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}
      <div className="mt-3 flex items-start gap-1.5 text-[11px] text-muted-foreground">
        <Info className="mt-0.5 size-3 shrink-0" />
        شماره‌ای بی‌صاحب می‌ماند تا کسی با آن وارد شود یا اینجا صاحبش مشخص شود؛ ورود، Import کوکی و ثبت فورواردر هرگز مالکیت را عوض نمی‌کنند.
      </div>
    </Section>
  );
}
