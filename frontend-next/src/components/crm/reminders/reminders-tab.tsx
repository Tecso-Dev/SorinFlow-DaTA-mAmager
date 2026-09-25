"use client";

// یادآورها — reminder list, sent filter, and the create/edit dialog.
// GET/POST /crm/reminders, PATCH/DELETE /crm/reminders/{id}.

import { Bell, Loader2, MessageSquareText, PenLine, Plus, Repeat, Trash2 } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { toast } from "@/components/toaster";
import { Reveal } from "@/components/viz";
import { JalaliDateInput } from "@/components/panel/date-input";
import {
  Empty, ErrorNote, Field, ListSkeleton, NativeSelect, PageHeader, RingDialog, Section, ToneBadge, Toolbar,
  useConfirm,
} from "@/components/panel/kit";
import { api, ApiError } from "@/lib/api";
import { qs, REMINDER_CHANNEL, REMINDER_REPEAT } from "@/lib/crm";
import { faDate, parseDigits } from "@/lib/format";

type Reminder = {
  id: number;
  title: string;
  remind_at: string | null;
  repeat: string;
  channel: string;
  sms_to: string | null;
  contact_id: number | null;
  deal_id: number | null;
  task_id: number | null;
  is_sent: boolean;
  created_at: string | null;
};

const QKEY = ["crm", "reminders"] as const;

function when(iso: string | null) {
  if (!iso) return "—";
  return faDate(new Date(iso), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/* ───────────────────────── create / edit dialog ───────────────────────── */

function ReminderDialog({
  open, onOpenChange, reminder,
}: { open: boolean; onOpenChange: (o: boolean) => void; reminder: Reminder | null }) {
  const qc = useQueryClient();
  const [title, setTitle] = useState(reminder?.title ?? "");
  const [remindAt, setRemindAt] = useState<Date | null>(reminder?.remind_at ? new Date(reminder.remind_at) : null);
  const [repeat, setRepeat] = useState(reminder?.repeat ?? "none");
  const [channel, setChannel] = useState(reminder?.channel ?? "in_app");
  const [smsTo, setSmsTo] = useState(reminder?.sms_to ?? "");
  const [busy, setBusy] = useState(false);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) {
      toast.error("عنوان یادآور لازم است");
      return;
    }
    if (!remindAt) {
      toast.error("زمان یادآوری لازم است");
      return;
    }
    if (channel === "sms" && !smsTo.trim()) {
      toast.error("شمارهٔ پیامک لازم است");
      return;
    }
    setBusy(true);
    try {
      const payload = {
        title: title.trim(),
        remind_at: remindAt.toISOString(),
        repeat,
        channel,
        sms_to: channel === "sms" ? parseDigits(smsTo.trim()) : null,
      };
      if (reminder) await api(`/crm/reminders/${reminder.id}`, { method: "PATCH", json: payload });
      else await api("/crm/reminders", { json: payload });
      await qc.invalidateQueries({ queryKey: QKEY });
      toast.success(reminder ? "یادآور ذخیره شد" : "یادآور ثبت شد");
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "ذخیره نشد");
    } finally {
      setBusy(false);
    }
  }

  return (
    <RingDialog open={open} onOpenChange={onOpenChange} icon={reminder ? PenLine : Plus} title={reminder ? "ویرایش یادآور" : "یادآور تازه"} wide>
      <form onSubmit={save} className="grid gap-3 sm:grid-cols-2">
        <Field label="عنوان" htmlFor="rem-title" className="sm:col-span-2">
          <Input id="rem-title" value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
        </Field>
        <Field label="زمان یادآوری">
          <JalaliDateInput value={remindAt} onChange={setRemindAt} withTime aria-label="زمان یادآوری" />
        </Field>
        <Field label="تکرار" htmlFor="rem-repeat">
          <NativeSelect id="rem-repeat" value={repeat} onChange={(e) => setRepeat(e.target.value)}>
            {Object.entries(REMINDER_REPEAT).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </NativeSelect>
        </Field>
        <Field label="کانال" htmlFor="rem-channel">
          <NativeSelect id="rem-channel" value={channel} onChange={(e) => setChannel(e.target.value)}>
            {Object.entries(REMINDER_CHANNEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </NativeSelect>
        </Field>
        {channel === "sms" && (
          <Field label="شمارهٔ گیرنده" htmlFor="rem-sms-to">
            <Input id="rem-sms-to" dir="ltr" inputMode="numeric" value={smsTo} onChange={(e) => setSmsTo(e.target.value)} placeholder="09xxxxxxxxx" />
          </Field>
        )}
        <div className="grid gap-2 sm:col-span-2">
          <Button type="submit" disabled={busy} className="w-full">
            {busy && <Loader2 className="size-4 animate-spin" />}
            {reminder ? "ذخیرهٔ تغییرات" : "ثبت یادآور"}
          </Button>
          <Button type="button" variant="ghost" className="w-full" onClick={() => onOpenChange(false)}>انصراف</Button>
        </div>
      </form>
    </RingDialog>
  );
}

/* ───────────────────────── page ───────────────────────── */

export function RemindersTab() {
  const [sent, setSent] = useState<"" | "active" | "sent">("");
  const [dialogReminder, setDialogReminder] = useState<Reminder | null | undefined>(undefined);
  const qc = useQueryClient();
  const confirm = useConfirm();

  const q = useQuery({
    queryKey: [...QKEY, sent],
    queryFn: () => {
      const is_sent = sent === "active" ? "false" : sent === "sent" ? "true" : "";
      return api<{ items: Reminder[]; total: number }>(`/crm/reminders${qs({ is_sent, limit: 200 })}`);
    },
  });

  async function remove(r: Reminder) {
    if (!(await confirm({ title: "حذف یادآور", description: `«${r.title}» حذف شود؟`, confirm: "حذف", danger: true, icon: Trash2 }))) return;
    try {
      await api(`/crm/reminders/${r.id}`, { method: "DELETE" });
      await qc.invalidateQueries({ queryKey: QKEY });
      toast.success("یادآور حذف شد");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "حذف نشد");
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        icon={Bell}
        title="یادآورها"
        hint={q.data ? `${q.data.total} یادآور` : undefined}
        actions={
          <Button className="gap-1.5" onClick={() => setDialogReminder(null)}>
            <Plus className="size-4" /> یادآور تازه
          </Button>
        }
      />

      <Reveal>
        <Section>
          <Toolbar className="mb-4">
            <Field label="وضعیت" htmlFor="reminders-filter-sent" className="w-44">
              <NativeSelect id="reminders-filter-sent" value={sent} onChange={(e) => setSent(e.target.value as "" | "active" | "sent")}>
                <option value="">همه</option>
                <option value="active">فعال</option>
                <option value="sent">ارسال‌شده</option>
              </NativeSelect>
            </Field>
          </Toolbar>

          {q.isPending ? (
            <ListSkeleton />
          ) : q.isError ? (
            <ErrorNote error={q.error} />
          ) : q.data.items.length === 0 ? (
            <Empty icon={Bell} action={<Button size="sm" onClick={() => setDialogReminder(null)}>یادآور تازه</Button>}>
              یادآوری ثبت نشده است.
            </Empty>
          ) : (
            <Table aria-label="فهرست یادآورها">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>عنوان</TableHead>
                  <TableHead>زمان</TableHead>
                  <TableHead>کانال</TableHead>
                  <TableHead>تکرار</TableHead>
                  <TableHead>وضعیت</TableHead>
                  <TableHead className="text-center">عملیات</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {q.data.items.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="max-w-[220px] truncate font-medium">{r.title}</TableCell>
                    <TableCell className="tabular">{when(r.remind_at)}</TableCell>
                    <TableCell>
                      <span className="inline-flex items-center gap-1">
                        {r.channel === "sms" && <MessageSquareText className="size-3.5 text-muted-foreground" />}
                        {REMINDER_CHANNEL[r.channel] ?? r.channel}
                      </span>
                      {r.channel === "sms" && r.sms_to && <div className="text-[11px] text-muted-foreground tabular">{r.sms_to}</div>}
                    </TableCell>
                    <TableCell>
                      {r.repeat !== "none" && <Repeat className="me-1 inline size-3 text-muted-foreground" />}
                      {REMINDER_REPEAT[r.repeat] ?? r.repeat}
                    </TableCell>
                    <TableCell>
                      <ToneBadge tone={r.is_sent ? "success" : "warning"}>{r.is_sent ? "ارسال‌شده" : "فعال"}</ToneBadge>
                    </TableCell>
                    <TableCell>
                      <div className="flex justify-center gap-1">
                        <Button variant="ghost" size="icon" className="size-8" aria-label="ویرایش یادآور" onClick={() => setDialogReminder(r)}>
                          <PenLine className="size-4" />
                        </Button>
                        <Button variant="ghost" size="icon" className="size-8 text-destructive" aria-label="حذف یادآور" onClick={() => remove(r)}>
                          <Trash2 className="size-4" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Section>
      </Reveal>

      {dialogReminder !== undefined && (
        <ReminderDialog open={dialogReminder !== undefined} onOpenChange={(o) => !o && setDialogReminder(undefined)} reminder={dialogReminder} />
      )}
    </div>
  );
}
