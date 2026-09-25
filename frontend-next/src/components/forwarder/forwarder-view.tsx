"use client";

// «فرستندهٔ پیامک»: the phones that hand Divar's SMS codes to the scraper.
// Scoped entirely to the caller by the backend (app/api/routes/forwarder.py
// filters every query by user_id), so this page never has to ask «whose
// device is this» — everything it lists is the signed-in user's own.

import {
  BookOpenText, KeyRound, MoreVertical, PenLine, Plus, RefreshCw, ScanLine, Smartphone, Trash2,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "motion/react";
import { useState } from "react";
import { cn } from "cn";
import {
  Empty, ErrorNote, ListSkeleton, PageHeader, Section, ToneBadge, useConfirm,
} from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api, ApiError } from "@/lib/api";
import { faDate, faNum } from "@/lib/format";
import { can, useSession } from "@/lib/session";
import { AddDeviceDialog } from "./add-device-dialog";
import { CodesLog } from "./codes-log";
import { EditSimDialog } from "./edit-sim-dialog";
import { GuideDialog } from "./guide-dialog";
import { HEALTH_TONE } from "./shared";
import type { Device, DevicesResponse, TestResult } from "./types";

function HealthDot({ state }: { state: Device["health"]["state"] }) {
  const cls: Record<Device["health"]["state"], string> = {
    ok: "bg-success", no_codes_yet: "bg-info", offline: "bg-warning", never_seen: "bg-muted-foreground", disabled: "bg-destructive",
  };
  return (
    <span className="relative inline-flex size-2" aria-hidden>
      {state === "ok" && (
        <motion.span
          className={cn("absolute inline-flex size-full rounded-full", cls[state])}
          animate={{ scale: [1, 2.2], opacity: [0.6, 0] }}
          transition={{ duration: 1.6, repeat: Infinity, ease: "easeOut" }}
        />
      )}
      <span className={cn("relative inline-flex size-2 rounded-full", cls[state])} />
    </span>
  );
}

function SimList({ d }: { d: Device }) {
  const sims = [d.sim_phone, d.sim_phone2].filter(Boolean) as string[];
  if (!sims.length) return <span className="text-muted-foreground">—</span>;
  return (
    <div dir="ltr" className="flex flex-col gap-0.5 text-start tabular">
      {sims.map((s, i) => <span key={s}>{i === 0 ? s : `${s} (سیم ۲)`}</span>)}
    </div>
  );
}

export function ForwarderView() {
  const user = useSession().data?.user;
  const canSms = can(user, { perm: "sms" });
  const qc = useQueryClient();
  const confirm = useConfirm();

  const [adding, setAdding] = useState(false);
  const [guideId, setGuideId] = useState<number | null>(null);
  const [editing, setEditing] = useState<Device | null>(null);

  const list = useQuery({
    queryKey: ["forwarder", "devices"],
    queryFn: () => api<DevicesResponse>("/forwarder/devices"),
    refetchInterval: 20_000,
  });
  const devices = list.data?.devices ?? [];

  const del = useMutation({
    mutationFn: (id: number) => api(`/forwarder/devices/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("دستگاه حذف شد");
      qc.invalidateQueries({ queryKey: ["forwarder", "devices"] });
    },
    onError: (e) => toast.error("حذف نشد", e instanceof ApiError ? e.message : undefined),
  });

  const rotate = useMutation({
    mutationFn: (id: number) => api<Device>(`/forwarder/devices/${id}/rotate`, { method: "POST" }),
    onSuccess: (d) => {
      toast.success("کلید تازه ساخته شد");
      qc.invalidateQueries({ queryKey: ["forwarder", "devices"] });
      qc.invalidateQueries({ queryKey: ["forwarder", "config", d.id] });
      setGuideId(d.id);
    },
    onError: (e) => toast.error("انجام نشد", e instanceof ApiError ? e.message : undefined),
  });

  const test = useMutation({
    mutationFn: (id: number) => api<TestResult>(`/forwarder/devices/${id}/test`, { method: "POST" }),
    onSuccess: (r) => {
      toast[r.ok ? "success" : "info"](r.ok ? "گوشی وصل است" : "هنوز خبری از گوشی نیست", r.hint_fa);
      qc.invalidateQueries({ queryKey: ["forwarder", "devices"] });
    },
    onError: (e) => toast.error("بررسی انجام نشد", e instanceof ApiError ? e.message : undefined),
  });

  async function onDelete(d: Device) {
    const ok = await confirm({
      title: `«${d.label || d.device_id}» حذف شود؟`,
      description: "این گوشی دیگر کدی برای این حساب نمی‌فرستد. این کار برگشت‌پذیر نیست.",
      confirm: "حذف",
      danger: true,
      icon: Trash2,
    });
    if (ok) del.mutate(d.id);
  }

  async function onRotate(d: Device) {
    const ok = await confirm({
      title: "کلید تازه ساخته شود؟",
      description: "کلید فعلی همین الان از کار می‌افتد و این گوشی تا وارد کردن کلید تازه در برنامه، هیچ کدی نمی‌فرستد.",
      confirm: "بله، کلید تازه",
      danger: true,
      icon: KeyRound,
    });
    if (ok) rotate.mutate(d.id);
  }

  const actionsFor = (d: Device) => (
    <DropdownMenu dir="rtl">
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label={`کارهای گوشی ${d.label || d.device_id}`}>
          <MoreVertical />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuItem onSelect={() => setGuideId(d.id)}><BookOpenText /> راهنمای نصب</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => test.mutate(d.id)}><ScanLine /> بررسی اتصال</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => onRotate(d)}><KeyRound /> کلید تازه</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => setEditing(d)}><PenLine /> ویرایش سیم‌کارت</DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onSelect={() => onDelete(d)}><Trash2 /> حذف دستگاه</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        icon={Smartphone}
        title="فرستندهٔ پیامک"
        hint="گوشی‌هایی که کد پیامکی دیوار را برای شما می‌فرستند"
        actions={
          <>
            <Button variant="ghost" size="icon" aria-label="بارگیری دوباره" onClick={() => list.refetch()} disabled={list.isFetching}>
              <RefreshCw className={cn(list.isFetching && "animate-spin")} />
            </Button>
            <Button onClick={() => setAdding(true)} className="shadow-[0_8px_24px_-8px_rgb(99_102_241/0.8)]">
              <Plus /> افزودن گوشی
            </Button>
          </>
        }
      />

      <Section title="گوشی‌های من" hint={devices.length ? `${faNum(devices.length)} دستگاه` : undefined}>
        {list.isLoading ? (
          <ListSkeleton rows={3} />
        ) : list.isError ? (
          <ErrorNote error={list.error} />
        ) : !devices.length ? (
          <Empty icon={Smartphone} action={<Button size="sm" onClick={() => setAdding(true)}><Plus /> افزودن گوشی</Button>}>
            هنوز گوشی‌ای ثبت نکرده‌اید. یک گوشی اضافه کنید تا کدهای دیوار خودکار برسند.
          </Empty>
        ) : (
          <>
            {/* desktop: table */}
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="border-b bg-muted/40 text-start text-xs text-muted-foreground">
                    <th className="px-3 py-2 text-start font-medium">نام</th>
                    <th className="px-3 py-2 text-start font-medium">سیم‌کارت‌ها</th>
                    <th className="px-3 py-2 text-start font-medium">وضعیت</th>
                    <th className="px-3 py-2 text-start font-medium">آخرین کد</th>
                    <th className="px-3 py-2 text-start font-medium">تعداد کد</th>
                    <th className="w-10 px-3 py-2"><span className="sr-only">عملیات</span></th>
                  </tr>
                </thead>
                <tbody>
                  {devices.map((d) => (
                    <tr key={d.id} className="border-b transition-colors hover:bg-muted/30">
                      <td className="px-3 py-2.5 font-semibold">{d.label || d.device_id}</td>
                      <td className="px-3 py-2.5"><SimList d={d} /></td>
                      <td className="px-3 py-2.5">
                        <span className="inline-flex items-center gap-1.5">
                          <HealthDot state={d.health.state} />
                          <ToneBadge tone={HEALTH_TONE[d.health.state]}>{d.health.message_fa}</ToneBadge>
                        </span>
                      </td>
                      <td className="px-3 py-2.5 whitespace-nowrap text-muted-foreground">
                        {d.last_code_at ? faDate(new Date(d.last_code_at), { dateStyle: "short", timeStyle: "short" }) : "—"}
                      </td>
                      <td className="px-3 py-2.5 tabular">{faNum(d.codes_forwarded)}</td>
                      <td className="px-3 py-2.5">{actionsFor(d)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* phone: cards */}
            <ul className="grid gap-2 md:hidden">
              {devices.map((d) => (
                <li key={d.id} className="rounded-xl border bg-background/50 p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-bold">{d.label || d.device_id}</div>
                      <SimList d={d} />
                    </div>
                    {actionsFor(d)}
                  </div>
                  <div className="mt-2 flex items-center justify-between gap-2">
                    <span className="inline-flex items-center gap-1.5 text-xs">
                      <HealthDot state={d.health.state} />
                      <ToneBadge tone={HEALTH_TONE[d.health.state]}>{d.health.message_fa}</ToneBadge>
                    </span>
                    <span className="text-[11px] text-muted-foreground tabular">{faNum(d.codes_forwarded)} کد</span>
                  </div>
                </li>
              ))}
            </ul>
          </>
        )}
      </Section>

      {canSms ? (
        <CodesLog />
      ) : (
        <Section title="کدهای رسیده از گوشی">
          <Empty>این بخش نیاز به دسترسی «پیامک» دارد که حساب شما ندارد.</Empty>
        </Section>
      )}

      <AddDeviceDialog open={adding} onOpenChange={setAdding} onOpenGuide={(id) => setGuideId(id)} />
      <GuideDialog id={guideId} onOpenChange={(o) => !o && setGuideId(null)} />
      <EditSimDialog device={editing} onOpenChange={(o) => !o && setEditing(null)} />
    </div>
  );
}
