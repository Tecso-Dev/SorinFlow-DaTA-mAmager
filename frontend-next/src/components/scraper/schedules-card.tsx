"use client";

// «اسکرپ‌های زمان‌بندی‌شده»: only shown once at least one exists. Root and
// super_admin see and manage everyone's (can_see_all); everyone else only
// their own — the same split the backend enforces on the same endpoints.

import { Clock, Pencil, Play, Trash2 } from "lucide-react";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Field, RingDialog, Section, useConfirm } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { api, ApiError } from "@/lib/api";
import { faNum } from "@/lib/format";
import type { Schedule, SchedulesResponse } from "./types";

const LAST_RESULT_TONE: Record<string, string> = { started: "text-success", failed: "text-destructive", skipped: "text-warning" };

function fa(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return `${d.toLocaleDateString("fa-IR")} ${d.toLocaleTimeString("fa-IR", { hour: "2-digit", minute: "2-digit" })}`;
}

export function SchedulesCard({ onRanNow }: { onRanNow: () => void }) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [editing, setEditing] = useState<Schedule | null>(null);

  const list = useQuery({
    queryKey: ["scraper", "schedules"],
    queryFn: () => api<SchedulesResponse>("/scraper/schedules"),
    refetchInterval: 60_000,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["scraper", "schedules"] });

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) => api<Schedule>(`/scraper/schedules/${id}`, { method: "PATCH", json: { enabled } }),
    onSuccess: (_r, v) => {
      toast.success(v.enabled ? "روشن شد" : "خاموش شد");
      invalidate();
    },
    onError: (e) => {
      toast.error("انجام نشد", e instanceof ApiError ? e.message : undefined);
      invalidate();
    },
  });

  const editTime = useMutation({
    mutationFn: ({ id, hour, minute }: { id: number; hour: number; minute: number }) =>
      api<Schedule>(`/scraper/schedules/${id}`, { method: "PATCH", json: { hour, minute } }),
    onSuccess: () => {
      toast.success("ساعت تغییر کرد");
      invalidate();
      setEditing(null);
    },
    onError: (e) => toast.error("انجام نشد", e instanceof ApiError ? e.message : undefined),
  });

  const runNow = useMutation({
    mutationFn: (id: number) => api<{ schedule: Schedule; status?: string; detail?: string }>(`/scraper/schedules/${id}/run`, { method: "POST" }),
    onSuccess: (r) => {
      toast.success(r.status === "started" || !r.status ? "شروع شد" : "اجرا نشد", r.detail);
      invalidate();
      onRanNow();
    },
    onError: (e) => toast.error("خطا", e instanceof ApiError ? e.message : undefined),
  });

  const del = useMutation({
    mutationFn: (id: number) => api(`/scraper/schedules/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("حذف شد");
      invalidate();
    },
    onError: (e) => toast.error("حذف نشد", e instanceof ApiError ? e.message : undefined),
  });

  async function onDelete(s: Schedule) {
    const ok = await confirm({
      title: "حذف زمان‌بندی",
      description: `«${s.name}» حذف شود؟ اسکرپ‌های قبلی‌اش می‌مانند.`,
      confirm: "حذف",
      danger: true,
      icon: Trash2,
    });
    if (ok) del.mutate(s.id);
  }

  // fails silently: the schedules card is a bonus, not a reason to block
  // the rest of the section, and it hides itself with no rows anyway
  const rows = list.data?.schedules ?? [];
  if (list.isLoading || list.isError || rows.length === 0) return null;

  return (
    <Section title="اسکرپ‌های زمان‌بندی‌شده" hint={`${faNum(rows.length)} زمان‌بندی`}>
        <div className="grid gap-2">
          {rows.map((s) => {
            const cfg = s.config;
            const what = [
              [s.city_name || cfg.city, s.category_name || cfg.category].filter(Boolean).join(" / "),
              cfg.max_items ? `${faNum(cfg.max_items)} آگهی` : "",
              cfg.max_age_hours ? `${faNum(cfg.max_age_hours)} ساعت اخیر` : "",
            ].filter(Boolean).join(" · ");
            const hh = String(s.hour).padStart(2, "0");
            const mm = String(s.minute).padStart(2, "0");
            const lr = s.last_result;
            return (
              <div key={s.id} className={`flex flex-wrap items-center gap-3 rounded-xl border p-3 ${s.enabled ? "" : "opacity-60"}`}>
                <div className="min-w-0 flex-1">
                  <div className="font-semibold">
                    {s.name}
                    {s.owner_name && <span className="ms-1.5 text-xs text-muted-foreground">({s.owner_name})</span>}
                  </div>
                  <div className="text-xs text-muted-foreground">{what || "—"}</div>
                  {s.last_run_at ? (
                    <div className={`text-xs ${LAST_RESULT_TONE[lr?.status ?? ""] ?? "text-muted-foreground"}`}>
                      {lr?.detail || lr?.status || ""} · {fa(s.last_run_at)}
                    </div>
                  ) : (
                    <div className="text-xs text-muted-foreground">هنوز اجرا نشده</div>
                  )}
                </div>
                <div dir="ltr" className="tabular text-sm font-semibold">
                  {hh}:{mm}
                  <div className="text-end text-[11px] font-normal text-muted-foreground">بعدی: {fa(s.next_run_at)}</div>
                </div>
                <Switch checked={s.enabled} disabled={toggle.isPending} onCheckedChange={(v) => toggle.mutate({ id: s.id, enabled: v })} aria-label={`روشن/خاموش ${s.name}`} />
                <div className="flex items-center gap-1">
                  <Button variant="ghost" size="icon-sm" title="اجرای فوری" aria-label="اجرای فوری" onClick={() => runNow.mutate(s.id)} disabled={runNow.isPending}>
                    <Play />
                  </Button>
                  <Button variant="ghost" size="icon-sm" title="تغییر ساعت" aria-label="تغییر ساعت" onClick={() => setEditing(s)}>
                    <Pencil />
                  </Button>
                  <Button variant="ghost" size="icon-sm" title="حذف" aria-label="حذف" onClick={() => onDelete(s)}>
                    <Trash2 />
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      <p className="mt-3 border-t pt-3 text-xs leading-6 text-muted-foreground">
        هر زمان‌بندی با حساب‌های دیوار <b>صاحبش</b> اجرا می‌شود — همان قانونی که اسکرپ دستی دارد. اگر «حداکثر سن آگهی» را خالی گذاشته باشید، فقط آگهی‌های ۲۴ ساعت اخیر گرفته می‌شود.
      </p>

      <EditTimeDialog schedule={editing} onClose={() => setEditing(null)} onSave={(h, m) => editing && editTime.mutate({ id: editing.id, hour: h, minute: m })} pending={editTime.isPending} />
    </Section>
  );
}

function EditTimeDialog({
  schedule, onClose, onSave, pending,
}: {
  schedule: Schedule | null;
  onClose: () => void;
  onSave: (hour: number, minute: number) => void;
  pending: boolean;
}) {
  const [time, setTime] = useState("08:00");
  // loads the schedule's own hour the moment it is opened, adjusted during render
  const [seenId, setSeenId] = useState<number | null>(null);
  if (schedule && schedule.id !== seenId) {
    setSeenId(schedule.id);
    setTime(`${String(schedule.hour).padStart(2, "0")}:${String(schedule.minute).padStart(2, "0")}`);
  }
  return (
    <RingDialog open={!!schedule} onOpenChange={(o) => !o && onClose()} icon={Clock} title="ساعت اجرا" description="به وقت تهران.">
      <form
        className="grid gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          const [h, m] = time.split(":").map(Number);
          onSave(h, m);
        }}
      >
        <Field label="ساعت" htmlFor="edit-sched-time">
          <Input id="edit-sched-time" type="time" dir="ltr" value={time} onChange={(e) => setTime(e.target.value)} className="tabular" />
        </Field>
        <Button type="submit" className="w-full" disabled={pending}>ذخیره</Button>
        <Button type="button" variant="ghost" className="w-full" onClick={onClose}>انصراف</Button>
      </form>
    </RingDialog>
  );
}
