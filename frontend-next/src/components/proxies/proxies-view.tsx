"use client";

// «پراکسی‌ها»: the simplest of the four scraper/divar sections — no OTP, no
// polling, no per-user ownership. Proxies are global, shared by every scrape,
// and every role with the `proxies` permission sees and manages all of them
// (backend: app/api/routes/proxies.py, app/auth/permissions.py).
//
// GET /proxies (ProxyResponse) never carries exit_country/exit_ip/is_hosting
// — only POST .../test and POST .../test-all return them, for the rows they
// just probed. So instead of invalidating the whole list after a test (which
// would refetch a response that forgets what was just learned), test and
// test-all patch the query cache directly for the rows they touched. An
// import can add rows GET never named, so that one does refetch — and any
// row's exit badge then honestly reverts to "untested" until it is tested
// again in this session, exactly as the data says.

import {
  Globe2, Loader2, Plus, RefreshCw, ShieldCheck, ShieldPlus, Trash2, Upload,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, useReducedMotion } from "motion/react";
import { useState } from "react";
import { cn } from "cn";
import { ErrorNote, ListSkeleton, PageHeader, Section, Toolbar, useConfirm } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { CountUp, Reveal } from "@/components/viz";
import { api, ApiError } from "@/lib/api";
import { faNum } from "@/lib/format";
import { AddProxyDialog } from "./add-proxy-dialog";
import { ExitBadge } from "./exit-badge";
import { ImportProxiesDialog } from "./import-dialog";
import type { Proxy, ProxyList, ProxyProbe, TestAllResult } from "./types";

const QK = ["proxies"] as const;

export function ProxiesView() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  const [testingId, setTestingId] = useState<number | null>(null);

  const list = useQuery({
    queryKey: QK,
    queryFn: () => api<ProxyList>("/proxies?active_only=false"),
  });

  const items = list.data?.items ?? [];
  const total = list.data?.total ?? items.length;
  const activeCount = items.filter((p) => p.is_active).length;
  const workingCount = items.filter((p) => p.is_working).length;

  function patchRow(id: number, patch: Partial<Proxy>) {
    qc.setQueryData<ProxyList>(QK, (cur) => {
      if (!cur) return cur;
      return { ...cur, items: cur.items.map((p) => (p.id === id ? { ...p, ...patch } : p)) };
    });
  }
  function removeRow(id: number) {
    qc.setQueryData<ProxyList>(QK, (cur) => {
      if (!cur) return cur;
      const items2 = cur.items.filter((p) => p.id !== id);
      return { items: items2, total: Math.max(0, cur.total - 1) };
    });
  }

  const testOne = useMutation({
    mutationFn: (id: number) => api<ProxyProbe>(`/proxies/${id}/test`, { method: "POST" }),
    onMutate: (id) => setTestingId(id),
    onSuccess: (r, id) => {
      const cur = items.find((p) => p.id === id);
      patchRow(id, {
        is_working: r.success,
        avg_response_time: r.response_time ?? null,
        exit_country: r.exit_country ?? null,
        exit_ip: r.exit_ip ?? null,
        is_hosting: r.is_hosting ?? null,
        success_count: (cur?.success_count ?? 0) + (r.success ? 1 : 0),
        fail_count: (cur?.fail_count ?? 0) + (r.success ? 0 : 1),
      });
      toast[r.success ? "success" : "error"](
        r.success ? "تست موفق" : "پراکسی کار نمی‌کند",
        r.success ? `زمان پاسخ: ${(r.response_time ?? 0).toFixed(2)} ثانیه` : (r.error ?? undefined),
      );
    },
    onError: (e) => toast.error("تست انجام نشد", e instanceof ApiError ? e.message : undefined),
    onSettled: () => setTestingId(null),
  });

  const testAll = useMutation({
    mutationFn: () => api<TestAllResult>("/proxies/test-all", { method: "POST" }),
    onSuccess: (r) => {
      for (const one of r.results) {
        if (one.proxy_id == null) continue;
        patchRow(one.proxy_id, {
          is_working: one.success,
          avg_response_time: one.response_time ?? null,
          exit_country: one.exit_country ?? null,
          exit_ip: one.exit_ip ?? null,
          is_hosting: one.is_hosting ?? null,
        });
      }
      toast.success("تست همه انجام شد", `${faNum(r.working)} از ${faNum(r.total)} فعال، ${faNum(r.iranian)} با خروجی ایران`);
    },
    onError: (e) => toast.error("تست همه انجام نشد", e instanceof ApiError ? e.message : undefined),
  });

  const toggle = useMutation({
    mutationFn: (id: number) => api<{ success: boolean; is_active: boolean; message: string }>(`/proxies/${id}/toggle`, { method: "POST" }),
    onSuccess: (r, id) => patchRow(id, { is_active: r.is_active }),
    onError: (e) => toast.error("تغییر وضعیت انجام نشد", e instanceof ApiError ? e.message : undefined),
  });

  const remove = useMutation({
    mutationFn: (id: number) => api<{ success: boolean }>(`/proxies/${id}`, { method: "DELETE" }),
    onSuccess: (_r, id) => {
      removeRow(id);
      toast.success("پراکسی حذف شد");
    },
    onError: (e) => toast.error("حذف نشد", e instanceof ApiError ? e.message : undefined),
  });

  const wipe = useMutation({
    mutationFn: (confirmCount: number) => api<{ success: boolean; deleted: number }>(`/proxies?confirm_count=${confirmCount}`, { method: "DELETE" }),
    onSuccess: (r) => {
      qc.setQueryData<ProxyList>(QK, { items: [], total: 0 });
      toast.success(`${faNum(r.deleted)} پراکسی حذف شد`);
    },
    onError: (e) => {
      // 409: the count changed since it was drawn (another tab) — show the
      // server's own "refresh the page" message, and refresh the list.
      toast.error("حذف انجام نشد", e instanceof ApiError ? e.message : undefined);
      if (e instanceof ApiError && e.status === 409) qc.invalidateQueries({ queryKey: QK });
    },
  });

  async function onDeleteOne(p: Proxy) {
    const ok = await confirm({
      title: "حذف پراکسی",
      description: `${p.address}:${faNum(p.port, { useGrouping: false })} حذف شود؟`,
      confirm: "حذف",
      danger: true,
      icon: Trash2,
    });
    if (ok) remove.mutate(p.id);
  }

  async function onWipeAll() {
    const count = total;
    if (!count) return;
    const ok = await confirm({
      title: "حذف همهٔ پراکسی‌ها",
      description: `همهٔ ${faNum(count)} پراکسی حذف شود؟ این کار برگشت‌پذیر نیست.`,
      confirm: "حذف همه",
      danger: true,
      icon: Trash2,
    });
    if (ok) wipe.mutate(count);
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        icon={ShieldCheck}
        title="پراکسی‌ها"
        hint={list.data ? <><CountUp value={total} /> پراکسی، {faNum(activeCount)} روشن، {faNum(workingCount)} پاسخ‌گو</> : "پراکسی‌های اسکرپر، مشترک بین همه"}
        actions={
          <>
            <Button variant="outline" onClick={() => setImporting(true)}>
              <Upload /> وارد کردن دسته‌ای
            </Button>
            <Button onClick={() => setAdding(true)} className="shadow-[0_8px_24px_-8px_rgb(99_102_241/0.8)]">
              <Plus /> افزودن پراکسی
            </Button>
          </>
        }
      />

      {!!items.length && (
        <Reveal>
          <div className="grid grid-cols-3 gap-2 sm:gap-3">
            <StatTile label="کل پراکسی‌ها" value={total} />
            <StatTile label="روشن" value={activeCount} />
            <StatTile label="پاسخ‌گو" value={workingCount} />
          </div>
        </Reveal>
      )}

      <Section
        bodyClassName="p-0 pt-0"
        className="overflow-hidden"
        title="لیست پراکسی‌ها"
        action={
          <Toolbar className="gap-1.5">
            <TestAllButton pending={testAll.isPending} disabled={!items.length} onClick={() => testAll.mutate()} />
            <Button
              size="sm"
              variant="outline"
              aria-label="حذف همهٔ پراکسی‌ها"
              className="text-destructive hover:text-destructive"
              disabled={!items.length || wipe.isPending}
              onClick={onWipeAll}
            >
              {wipe.isPending ? <Loader2 className="animate-spin" /> : <Trash2 />} <span className="hidden sm:inline">حذف همه</span>
            </Button>
            <Button size="sm" variant="ghost" onClick={() => list.refetch()} disabled={list.isFetching} aria-label="بارگیری دوباره">
              <RefreshCw className={cn(list.isFetching && "animate-spin")} />
            </Button>
          </Toolbar>
        }
      >
        {list.isLoading ? (
          <div className="p-5"><ListSkeleton rows={5} /></div>
        ) : list.isError ? (
          <div className="p-5"><ErrorNote error={list.error} /></div>
        ) : !items.length ? (
          <div className="p-5">
            <ProxiesEmpty onAdd={() => setAdding(true)} onImport={() => setImporting(true)} />
          </div>
        ) : (
          <>
            {/* desktop: the table */}
            <div className="hidden md:block">
              <Table className="text-[13px]">
                <TableHeader>
                  <TableRow className="bg-muted/40 hover:bg-muted/40">
                    <TableHead>آدرس</TableHead>
                    <TableHead>پورت</TableHead>
                    <TableHead>وضعیت</TableHead>
                    <TableHead>خروجی</TableHead>
                    <TableHead>موفق / ناموفق</TableHead>
                    <TableHead>زمان پاسخ</TableHead>
                    <TableHead className="w-44 pe-4"><span className="sr-only">عملیات</span></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((p, i) => (
                    <motion.tr
                      key={p.id}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: Math.min(i, 12) * 0.02 }}
                      className="border-b transition-colors hover:bg-muted/40"
                    >
                      <TableCell className="whitespace-nowrap font-medium tabular" dir="ltr">{p.address}</TableCell>
                      <TableCell className="tabular" dir="ltr">{faNum(p.port, { useGrouping: false })}</TableCell>
                      <TableCell>
                        <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold", p.is_working ? "bg-success/12 text-success" : "bg-destructive/12 text-destructive")}>
                          {p.is_working ? "فعال" : "غیرفعال"}
                        </span>
                      </TableCell>
                      <TableCell><ExitBadge p={p} /></TableCell>
                      <TableCell className="tabular">
                        <span className="text-success">{faNum(p.success_count)}</span> / <span className="text-destructive">{faNum(p.fail_count)}</span>
                      </TableCell>
                      <TableCell className="tabular">{p.avg_response_time != null ? `${faNum(p.avg_response_time, { maximumFractionDigits: 2 })} ثانیه` : "—"}</TableCell>
                      <TableCell className="pe-4">
                        <RowActions p={p} testingId={testingId} onTest={() => testOne.mutate(p.id)} onToggle={() => toggle.mutate(p.id)} onDelete={() => onDeleteOne(p)} />
                      </TableCell>
                    </motion.tr>
                  ))}
                </TableBody>
              </Table>
            </div>

            {/* phone: cards */}
            <ul className="grid gap-2 p-3 md:hidden">
              {items.map((p, i) => (
                <motion.li
                  key={p.id}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: Math.min(i, 10) * 0.03 }}
                  className="rounded-xl border bg-background/50 p-3"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="font-semibold tabular" dir="ltr">{p.address}:{faNum(p.port, { useGrouping: false })}</div>
                      <div className="mt-0.5 text-[11px] uppercase text-muted-foreground">{p.protocol}</div>
                    </div>
                    <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold", p.is_working ? "bg-success/12 text-success" : "bg-destructive/12 text-destructive")}>
                      {p.is_working ? "فعال" : "غیرفعال"}
                    </span>
                  </div>
                  <div className="mt-2"><ExitBadge p={p} /></div>
                  <div className="mt-2 flex items-center justify-between gap-2 text-xs text-muted-foreground">
                    <span className="tabular">
                      <span className="text-success">{faNum(p.success_count)}</span> / <span className="text-destructive">{faNum(p.fail_count)}</span>
                      {" · "}
                      {p.avg_response_time != null ? `${faNum(p.avg_response_time, { maximumFractionDigits: 2 })} ثانیه` : "—"}
                    </span>
                  </div>
                  <div className="mt-2.5">
                    <RowActions p={p} testingId={testingId} onTest={() => testOne.mutate(p.id)} onToggle={() => toggle.mutate(p.id)} onDelete={() => onDeleteOne(p)} />
                  </div>
                </motion.li>
              ))}
            </ul>
          </>
        )}
      </Section>

      <AddProxyDialog open={adding} onOpenChange={setAdding} />
      <ImportProxiesDialog open={importing} onOpenChange={setImporting} />
    </div>
  );
}

/* ───────────────────────── pieces ───────────────────────── */

function StatTile({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-2xl border bg-card p-3 text-center dark:bg-linear-to-b dark:from-white/[0.035] dark:to-white/[0.008]">
      <div className="text-xl font-black tabular"><CountUp value={value} /></div>
      <div className="mt-0.5 text-[11px] text-muted-foreground">{label}</div>
    </div>
  );
}

/** «تست همه» — a soft indigo glow pulses around it while the run is live, so
 *  the one action that touches every row (and can take a while) reads as
 *  busy at a glance, not just via a spinning icon. Still smooth under
 *  prefers-reduced-motion: the glow is skipped, the spinner remains. */
function TestAllButton({ pending, disabled, onClick }: { pending: boolean; disabled: boolean; onClick: () => void }) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className="relative rounded-md"
      animate={pending && !reduce ? { boxShadow: ["0 0 0px 0px rgb(99 102 241 / 0.5)", "0 0 0px 8px rgb(99 102 241 / 0)"] } : { boxShadow: "0 0 0px 0px rgb(99 102 241 / 0)" }}
      transition={pending && !reduce ? { duration: 1.4, repeat: Infinity, ease: "easeOut" } : undefined}
    >
      <Button size="sm" variant="outline" aria-label="تست همهٔ پراکسی‌های روشن" disabled={disabled || pending} onClick={onClick}>
        {pending ? <Loader2 className="animate-spin" /> : <ShieldCheck />} <span className="hidden sm:inline">تست همه</span>
      </Button>
    </motion.div>
  );
}

function RowActions({
  p, testingId, onTest, onToggle, onDelete,
}: { p: Proxy; testingId: number | null; onTest: () => void; onToggle: () => void; onDelete: () => void }) {
  const busy = testingId === p.id;
  return (
    <div className="flex items-center justify-end gap-1">
      <Button variant="ghost" size="icon-sm" aria-label="تست پراکسی" title="تست" disabled={busy} onClick={onTest}>
        {busy ? <Loader2 className="animate-spin" /> : <ShieldPlus />}
      </Button>
      <label className="flex items-center gap-1.5 px-1" title={p.is_active ? "روشن — برای خاموش کردن بزنید" : "خاموش — برای روشن کردن بزنید"}>
        <span className="sr-only">{p.is_active ? "روشن" : "خاموش"}</span>
        <Switch checked={p.is_active} onCheckedChange={onToggle} aria-label={`پراکسی ${p.address} ${p.is_active ? "روشن" : "خاموش"}`} />
      </label>
      <Button variant="ghost" size="icon-sm" aria-label="حذف پراکسی" title="حذف" className="text-destructive hover:text-destructive" onClick={onDelete}>
        <Trash2 />
      </Button>
    </div>
  );
}

/** Nothing imported yet: an isometric server-rack, layered the same way the
 *  header's IsoBadge is, but drawn for this empty state specifically. */
function ProxiesEmpty({ onAdd, onImport }: { onAdd: () => void; onImport: () => void }) {
  return (
    <div className="flex min-h-52 flex-col items-center justify-center gap-4 rounded-xl border border-dashed px-4 py-10 text-center">
      <ServerRackIllustration />
      <div>
        <p className="font-semibold">هنوز پراکسی‌ای ثبت نشده</p>
        <p className="mt-1 text-sm text-muted-foreground">یک پراکسی اضافه کنید یا لیستی را دسته‌ای وارد کنید.</p>
      </div>
      <div className="flex flex-wrap items-center justify-center gap-2">
        <Button variant="outline" size="sm" onClick={onImport}><Upload /> وارد کردن دسته‌ای</Button>
        <Button size="sm" onClick={onAdd}><Plus /> افزودن پراکسی</Button>
      </div>
    </div>
  );
}

/** A tiny isometric server rack with a slowly-orbiting globe (the exit
 *  location a proxy is judged on). CSS/SVG only, static under reduced
 *  motion. */
function ServerRackIllustration() {
  const reduce = useReducedMotion();
  return (
    <div className="relative size-20" aria-hidden>
      {[4, 3, 2, 1].map((k) => (
        <div
          key={k}
          className="absolute inset-x-3 top-2 h-14 rounded-lg bg-violet-900/60 dark:bg-violet-950"
          style={{ transform: `translate(${k * 1.4}px, ${k * 1.4}px)`, opacity: 0.3 + (4 - k) * 0.12 }}
        />
      ))}
      <div className="absolute inset-x-3 top-2 flex h-14 flex-col justify-center gap-1.5 rounded-lg bg-linear-to-br from-indigo-400 via-indigo-500 to-violet-600 px-2.5 shadow-[0_12px_28px_-8px_rgb(99_102_241/0.8)]">
        {[0, 1, 2].map((r) => (
          <div key={r} className="flex items-center gap-1">
            <span className="size-1.5 rounded-full bg-white/70" />
            <span className="h-1 flex-1 rounded-full bg-white/25" />
          </div>
        ))}
      </div>
      <motion.div
        className="absolute -end-1 -top-1 grid size-7 place-items-center rounded-full bg-card shadow-md ring-2 ring-background"
        animate={reduce ? undefined : { rotate: 360 }}
        transition={reduce ? undefined : { duration: 9, repeat: Infinity, ease: "linear" }}
      >
        <Globe2 className="size-4 text-primary" />
      </motion.div>
    </div>
  );
}
