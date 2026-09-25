"use client";

// حالت تعمیر سایت — close/reopen with a duration, a countdown once closed,
// and the bypass link a second device can use without signing in first.
// «ذخیرهٔ تنظیمات» only re-applies while already closed (matches the old
// panel: it warns instead of calling the API while the site is open, since
// settings only take effect at the next close).

import { AlertTriangle, Ban, Loader2, Unlock } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field, NativeSelect, Section, useConfirm } from "@/components/panel/kit";
import { Reveal } from "@/components/viz";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { faNum } from "@/lib/format";

type MaintenanceState = {
  enabled: boolean; message: string; until: string | null; seconds_left: number | null;
  contact_phone: string | null; contact_email: string | null; bypass_active?: boolean;
};
type SetResult = MaintenanceState & { bypass_url: string | null };

const QKEY = ["users", "maintenance"] as const;
const DURATIONS = [
  { value: "1", label: "۱ ساعت" }, { value: "6", label: "۶ ساعت" }, { value: "24", label: "۱ روز" },
  { value: "72", label: "۳ روز" }, { value: "168", label: "۱ هفته" }, { value: "", label: "بدون شمارش معکوس" },
];

function countdown(sec: number) {
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  if (d > 0) return `${faNum(d)} روز و ${faNum(h)} ساعت`;
  if (h > 0) return `${faNum(h)} ساعت و ${faNum(m)} دقیقه`;
  return `${faNum(m)} دقیقه`;
}

export function MaintenanceCard() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const q = useQuery({ queryKey: QKEY, queryFn: () => api<MaintenanceState>("/maintenance") });

  const [message, setMessage] = useState("");
  const [hours, setHours] = useState("72");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [bypassUrl, setBypassUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState<"close" | "open" | "save" | null>(null);
  const [hydrated, setHydrated] = useState(false);

  if (q.data && !hydrated) {
    setMessage(q.data.message || "");
    setPhone(q.data.contact_phone || "");
    setEmail(q.data.contact_email || "");
    setHydrated(true);
  }

  async function apply(enabled: boolean) {
    setBusy(enabled ? "close" : "open");
    try {
      const r = await api<SetResult>("/maintenance", {
        method: "POST",
        json: {
          enabled, message: message.trim() || undefined,
          hours: hours ? Number(hours) : undefined,
          contact_phone: phone.trim() || undefined, contact_email: email.trim() || undefined,
        },
      });
      setBypassUrl(r.bypass_url);
      await qc.invalidateQueries({ queryKey: QKEY });
      toast.success(enabled ? "سایت بسته شد" : "سایت باز شد");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "ذخیره نشد");
    } finally {
      setBusy(null);
    }
  }

  async function closeSite() {
    if (!(await confirm({
      title: "بستن سایت", danger: true, confirm: "بستن سایت", icon: Ban,
      description: "سایت برای بازدیدکنندگان بسته می‌شود تا دوباره بازش کنید.",
    }))) return;
    apply(true);
  }

  async function saveOnly() {
    if (!q.data?.enabled) {
      toast.error("سایت باز است — تنظیمات فقط موقع بستن اعمال می‌شود");
      return;
    }
    setBusy("save");
    try {
      const r = await api<SetResult>("/maintenance", {
        method: "POST",
        json: {
          enabled: true, message: message.trim() || undefined,
          hours: hours ? Number(hours) : undefined,
          contact_phone: phone.trim() || undefined, contact_email: email.trim() || undefined,
        },
      });
      setBypassUrl(r.bypass_url);
      await qc.invalidateQueries({ queryKey: QKEY });
      toast.success("تنظیمات ذخیره شد");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "ذخیره نشد");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Reveal delay={0.05}>
      <Section
        title="حالت تعمیر سایت"
        action={q.data?.enabled ? <span className="text-xs font-bold text-destructive">بسته است</span> : <span className="text-xs font-bold text-success">باز است</span>}
      >
        {q.isPending ? (
          <div className="h-24 animate-pulse rounded-lg bg-muted/40" />
        ) : (
          <div className="flex flex-col gap-3">
            {q.data?.enabled && q.data.seconds_left != null && (
              <div className="flex items-center gap-2 rounded-lg bg-warning/12 px-3 py-2 text-sm text-warning">
                <AlertTriangle className="size-4 shrink-0" />
                تا بازگشایی خودکار: {countdown(q.data.seconds_left)}
              </div>
            )}
            <Field label="پیام بستن سایت" htmlFor="mt-message">
              <Input id="mt-message" value={message} onChange={(e) => setMessage(e.target.value)} placeholder="در حال بروزرسانی سامانه هستیم" />
            </Field>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <Field label="مدت" htmlFor="mt-hours">
                <NativeSelect id="mt-hours" value={hours} onChange={(e) => setHours(e.target.value)}>
                  {DURATIONS.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
                </NativeSelect>
              </Field>
              <Field label="شمارهٔ اضطراری" htmlFor="mt-phone">
                <Input id="mt-phone" dir="ltr" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="09xxxxxxxxx" />
              </Field>
              <Field label="ایمیل پشتیبانی" htmlFor="mt-email">
                <Input id="mt-email" dir="ltr" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="support@…" />
              </Field>
            </div>
            {bypassUrl && (
              <div className="rounded-lg border border-dashed p-3 text-xs">
                <div className="mb-1 font-semibold">لینک عبور برای دستگاه دیگر</div>
                <a href={bypassUrl} className="break-all text-primary hover:underline" dir="ltr">{bypassUrl}</a>
              </div>
            )}
            <div className="flex flex-wrap gap-2">
              {q.data?.enabled ? (
                <>
                  <Button size="sm" className="gap-1.5" disabled={!!busy} onClick={() => apply(false)}>
                    {busy === "open" ? <Loader2 className="size-4 animate-spin" /> : <Unlock className="size-4" />}
                    باز کردن سایت
                  </Button>
                  <Button size="sm" variant="outline" disabled={!!busy} onClick={saveOnly}>
                    {busy === "save" && <Loader2 className="size-4 animate-spin" />}
                    ذخیرهٔ تنظیمات
                  </Button>
                </>
              ) : (
                <Button size="sm" variant="destructive" className="gap-1.5" disabled={!!busy} onClick={closeSite}>
                  {busy === "close" ? <Loader2 className="size-4 animate-spin" /> : <Ban className="size-4" />}
                  بستن سایت
                </Button>
              )}
            </div>
          </div>
        )}
      </Section>
    </Reveal>
  );
}
