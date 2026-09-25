"use client";

// «نشست‌های ذخیره‌شده»: every Divar number this user owns — mine=1, never
// the whole pool (auth.py's _usable_by). Refresh and logout act on one
// number at a time (?phone_number=), which the old panel's single implicit
// button under the login form did not offer once a person has more than one
// number.

import { BadgeCheck, IdCard, LogOut, RefreshCw, ShieldOff, Trash2 } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Empty, ErrorNote, ListSkeleton, Section, ToneBadge, useConfirm } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { api, ApiError } from "@/lib/api";
import { faDate, faNum } from "@/lib/format";
import type { CookiesResponse, DivarCookie } from "./types";

function when(iso: string | null) {
  if (!iso) return null;
  return faDate(new Date(iso), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function SavedSessionsCard() {
  const qc = useQueryClient();
  const confirm = useConfirm();

  const list = useQuery({
    queryKey: ["divar", "cookies"],
    queryFn: () => api<CookiesResponse>("/auth/cookies?mine=1"),
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["divar", "cookies"] });
    qc.invalidateQueries({ queryKey: ["divar", "registry"] });
  };

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api<{ success: boolean; is_enabled: boolean; moved_jobs: string[] }>(`/auth/cookies/${id}`, {
        method: "PATCH",
        json: { enabled },
      }),
    onSuccess: (r) => {
      invalidate();
      if (r.moved_jobs.length) {
        toast.info("شماره خاموش شد", `${faNum(r.moved_jobs.length)} اسکرپ در حال اجرا به شمارهٔ دیگر این کاربر منتقل می‌شود`);
      } else {
        toast.success(r.is_enabled ? "شماره روشن شد" : "شماره خاموش شد");
      }
    },
    onError: (e) => toast.error("انجام نشد", e instanceof ApiError ? e.message : undefined),
  });

  const identityDone = useMutation({
    mutationFn: (id: number) => api<{ success: boolean }>(`/auth/cookies/${id}/identity-cleared`, { method: "POST" }),
    onSuccess: () => {
      invalidate();
      toast.success("ثبت شد", "این شماره دوباره در چرخش قرار می‌گیرد");
    },
    onError: (e) => toast.error("انجام نشد", e instanceof ApiError ? e.message : undefined),
  });

  const refresh = useMutation({
    mutationFn: (phone: string) => api<{ success: boolean; in_use?: boolean; message: string }>(`/auth/refresh?phone_number=${encodeURIComponent(phone)}`, { method: "POST" }),
    onSuccess: (r) => {
      invalidate();
      if (r.in_use) toast.info("نشست در حال استفاده است", r.message);
      else if (r.success) toast.success("نشست تازه شد", r.message);
      else toast.error("نشست منقضی است", r.message);
    },
    onError: (e) => toast.error("بازنشانی نشد", e instanceof ApiError ? e.message : undefined),
  });

  const logout = useMutation({
    mutationFn: (phone: string) => api<{ success: boolean; message: string }>(`/auth/logout?phone_number=${encodeURIComponent(phone)}`, { method: "POST" }),
    onSuccess: () => {
      invalidate();
      toast.success("خروج انجام شد");
    },
    onError: (e) => toast.error("خروج انجام نشد", e instanceof ApiError ? e.message : undefined),
  });

  const del = useMutation({
    mutationFn: (id: number) => api<{ success: boolean }>(`/auth/cookies/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      invalidate();
      toast.success("نشست حذف شد");
    },
    onError: (e) => toast.error("حذف نشد", e instanceof ApiError ? e.message : undefined),
  });

  async function askDelete(row: DivarCookie) {
    const ok = await confirm({
      title: "حذف نشست دیوار",
      description: `نشست شمارهٔ ${row.phone_number} حذف شود؟ این کار برگشت‌پذیر نیست و برای اسکرپ دوباره باید وارد شوید.`,
      confirm: "حذف",
      danger: true,
      icon: Trash2,
    });
    if (ok) del.mutate(row.id);
  }

  async function askLogout(row: DivarCookie) {
    const ok = await confirm({ title: "خروج از حساب", description: `از شمارهٔ ${row.phone_number} خارج شوید؟`, confirm: "خروج", icon: LogOut });
    if (ok) logout.mutate(row.phone_number);
  }

  const rows = list.data?.cookies ?? [];

  return (
    <Section title="نشست‌های ذخیره‌شده" hint="شماره‌های دیواری که همین حالا با این حساب پنل کار می‌کنند">
      {list.isLoading ? (
        <ListSkeleton rows={3} />
      ) : list.isError ? (
        <ErrorNote error={list.error} />
      ) : !rows.length ? (
        <Empty icon={BadgeCheck}>هنوز شمارهٔ دیواری وارد این حساب نشده است.</Empty>
      ) : (
        <ul className="grid gap-2">
          {rows.map((row) => (
            <li key={row.id} className="flex flex-col gap-2 rounded-xl border bg-background/50 p-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span dir="ltr" className="font-mono text-sm font-semibold">{row.phone_number}</span>
                  {row.is_valid ? <ToneBadge tone="success">معتبر</ToneBadge> : <ToneBadge tone="danger">منقضی</ToneBadge>}
                  {!row.is_enabled && <ToneBadge tone="neutral">خاموش</ToneBadge>}
                  {row.identity_required_at && <ToneBadge tone="warning">احراز هویت لازم</ToneBadge>}
                </div>
                <div className="mt-1 text-[11px] text-muted-foreground">
                  {faNum(row.reveals)} افشا
                  {row.last_checked_at && ` · آخرین بررسی ${when(row.last_checked_at)}`}
                </div>
                {row.identity_required_at && (
                  <Button size="xs" variant="outline" className="mt-1.5" disabled={identityDone.isPending} onClick={() => identityDone.mutate(row.id)}>
                    <IdCard /> انجام شد — احراز هویت کردم
                  </Button>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  روشن
                  <Switch
                    aria-label={`روشن یا خاموش کردن شمارهٔ ${row.phone_number}`}
                    checked={row.is_enabled}
                    disabled={toggle.isPending}
                    onCheckedChange={(v) => toggle.mutate({ id: row.id, enabled: v })}
                  />
                </label>
                <Button size="icon-sm" variant="ghost" aria-label={`بازنشانی نشست ${row.phone_number}`} disabled={refresh.isPending} onClick={() => refresh.mutate(row.phone_number)}>
                  <RefreshCw />
                </Button>
                <Button size="icon-sm" variant="ghost" aria-label={`خروج از ${row.phone_number}`} disabled={logout.isPending} onClick={() => askLogout(row)}>
                  <LogOut />
                </Button>
                <Button size="icon-sm" variant="ghost" className="text-destructive hover:text-destructive" aria-label={`حذف نشست ${row.phone_number}`} disabled={del.isPending} onClick={() => askDelete(row)}>
                  <Trash2 />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-3 flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <ShieldOff className="size-3" /> فقط شماره‌هایی که خودتان صاحبشان هستید اینجا دیده می‌شود.
      </div>
    </Section>
  );
}
