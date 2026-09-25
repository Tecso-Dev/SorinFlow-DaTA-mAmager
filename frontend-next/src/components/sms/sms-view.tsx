"use client";

// پیامک — stats, Kavenegar settings, service events, single send, broadcast
// and delivery history. Mirrors frontend/js/app.js loadSms() and friends
// against app/api/routes/sms.py.

import {
  BadgeCheck, Loader2, Megaphone, MessageSquareText, RefreshCw, Save, Search, Send, ShieldAlert, Wallet,
} from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/components/toaster";
import { Reveal, Tilt, CountUp } from "@/components/viz";
import {
  Empty, ErrorNote, Field, ListSkeleton, NativeSelect, PageHeader, Pagination, RingDialog, Section, ToneBadge,
  Toolbar,
} from "@/components/panel/kit";
import { api, ApiError } from "@/lib/api";
import { qs } from "@/lib/crm";
import { faDate, faNum, parseDigits } from "@/lib/format";
import { useSession, can, type User } from "@/lib/session";

/* ───────────────────────── types (app/api/routes/sms.py) ───────────────────────── */

type SmsSettings = {
  key_source: "env" | "panel" | null;
  api_key_masked: string;
  configured: boolean;
  sender: string;
  otp_template: string;
  signature: string;
  enabled: boolean;
  provider: string;
};
type SmsAccount = { ok: boolean; remaining_credit?: number; expire_date?: string; status?: number; error?: string };
type SmsStats = {
  total: number; last_30_days: number; failed_30_days: number; delivered_30_days: number;
  cost_30_days: number; success_rate: number | null;
};
type Audience = { key: string; label: string; count: number | null };
type SmsMessage = {
  id: number; to_number: string; message: string; status: string; provider: string; response: string | null;
  sent_at: string | null; message_id: string | null; cost: number | null; delivery_status: number | null;
  delivery_text: string | null; sent_by: string | null; campaign: string | null; kind: string;
};
type SmsEvent = {
  id: number; at: string | null; stage: string; level: string; message: string; route: string | null;
  status: string | null; actor: string | null; details: Record<string, unknown>;
};

const STAGE_LABEL: Record<string, string> = {
  send: "ارسال", test: "آزمایشی", settings: "تنظیمات", template: "قالب", error: "خطا", inbound: "دریافتی",
};
const LEVEL_TONE: Record<string, "neutral" | "warning" | "danger" | "info"> = {
  info: "info", warning: "warning", error: "danger",
};
const MSG_STATUS_TONE: Record<string, "success" | "danger" | "warning"> = {
  sent: "success", failed: "danger", pending: "warning",
};
const MSG_STATUS_LABEL: Record<string, string> = { sent: "ارسال شد", failed: "ناموفق", pending: "در صف" };

/** Persian SMS billing: 70 chars per single part, 67 per part once it splits. */
function smsParts(text: string): { len: number; parts: number } {
  const len = [...text].length;
  if (len === 0) return { len, parts: 0 };
  return { len, parts: len <= 70 ? 1 : Math.ceil(len / 67) };
}

/* ───────────────────────── tiles ───────────────────────── */

function Tiles({ account, stats }: { account?: SmsAccount; stats?: SmsStats }) {
  const items = [
    {
      key: "credit", label: "اعتبار باقی‌مانده", icon: Wallet,
      value: account?.ok ? account.remaining_credit ?? null : null,
      hint: account?.ok ? "تومان" : account?.error ?? "در دسترس نیست", tint: "text-primary bg-primary/12",
    },
    {
      key: "sent", label: "ارسال‌شده در ۳۰ روز", icon: Send, value: stats?.last_30_days ?? null,
      hint: stats ? `${faNum(stats.total)} کل` : "—", tint: "text-info bg-info/12",
    },
    {
      key: "delivered", label: "رسیده به گیرنده", icon: BadgeCheck, value: stats?.delivered_30_days ?? null,
      hint: stats?.success_rate !== null && stats?.success_rate !== undefined ? `${faNum(stats.success_rate)}٪ موفق` : "نمونه کم",
      tint: "text-success bg-success/12",
    },
    {
      key: "failed", label: "ناموفق در ۳۰ روز", icon: ShieldAlert, value: stats?.failed_30_days ?? null,
      hint: stats ? `${faNum(stats.cost_30_days)} تومان هزینه` : "—", tint: "text-destructive bg-destructive/12",
    },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
      {items.map((it, i) => (
        <Reveal key={it.key} delay={i * 0.05}>
          <Tilt className="flex h-full flex-col gap-3 rounded-2xl border bg-card p-4 shadow-sm dark:bg-linear-to-b dark:from-white/[0.04] dark:to-transparent dark:shadow-none">
            <div className={`grid size-10 shrink-0 place-items-center rounded-xl ring-1 ring-inset ring-current/20 shadow-[0_0_20px_-6px_currentColor] ${it.tint}`}>
              <it.icon className="size-5" />
            </div>
            <div>
              <div className="text-[13px] text-muted-foreground">{it.label}</div>
              <div className="mt-1 text-[24px] leading-none font-black tabular">
                {it.value === null ? "—" : <CountUp value={it.value} />}
              </div>
              <div className="mt-1.5 truncate text-[11px] text-muted-foreground">{it.hint}</div>
            </div>
          </Tilt>
        </Reveal>
      ))}
    </div>
  );
}

/* ───────────────────────── settings (super_admin) ───────────────────────── */

function SettingsCard() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["sms", "settings"], queryFn: () => api<SmsSettings>("/sms/settings") });
  const [apiKey, setApiKey] = useState("");
  const [sender, setSender] = useState<string | null>(null);
  const [otpTemplate, setOtpTemplate] = useState<string | null>(null);
  const [signature, setSignature] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [testTo, setTestTo] = useState("");
  const [testBusy, setTestBusy] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; via?: string; error?: string } | null>(null);

  const s = q.data;

  async function save() {
    setBusy(true);
    try {
      const body: Record<string, unknown> = {};
      if (apiKey.trim()) body.api_key = apiKey.trim();
      if (sender !== null) body.sender = sender;
      if (otpTemplate !== null) body.otp_template = otpTemplate;
      if (signature !== null) body.signature = signature;
      if (s) body.enabled = s.enabled;
      await api("/sms/settings", { method: "PUT", json: body });
      await qc.invalidateQueries({ queryKey: ["sms", "settings"] });
      setApiKey("");
      toast.success("تنظیمات پیامک ذخیره شد");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "ذخیره نشد");
    } finally {
      setBusy(false);
    }
  }

  async function toggleEnabled(next: boolean) {
    try {
      await api("/sms/settings", { method: "PUT", json: { enabled: next } });
      await qc.invalidateQueries({ queryKey: ["sms", "settings"] });
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "ذخیره نشد");
    }
  }

  async function sendTest() {
    if (!testTo.trim()) {
      toast.error("شمارهٔ گیرنده را وارد کنید");
      return;
    }
    setTestBusy(true);
    setTestResult(null);
    try {
      const res = await api<{ ok: boolean; via?: string; error?: string; message_id?: string }>(
        `/sms/test${qs({ to: parseDigits(testTo.trim()) })}`,
        { method: "POST" },
      );
      setTestResult(res);
      if (res.ok) toast.success("پیام آزمایشی ارسال شد", res.via);
      else toast.error("ارسال ناموفق بود", res.error);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "ارسال نشد");
    } finally {
      setTestBusy(false);
    }
  }

  return (
    <Section
      title="تنظیمات کاوه‌نگار"
      hint={s ? `کلید از ${s.key_source === "env" ? "متغیر محیطی" : s.key_source === "panel" ? "پنل" : "جایی تنظیم نشده"}` : undefined}
      action={s && <ToneBadge tone={s.configured ? "success" : "warning"}>{s.configured ? "پیکربندی‌شده" : "بدون کلید"}</ToneBadge>}
    >
      {q.isPending ? (
        <ListSkeleton rows={3} />
      ) : q.isError ? (
        <ErrorNote error={q.error} />
      ) : (
        <div className="grid gap-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="کلید API" htmlFor="sms-api-key" hint={`فعلی: ${s!.api_key_masked || "—"}`}>
              <Input id="sms-api-key" dir="ltr" type="password" placeholder="برای تغییر وارد کنید" value={apiKey} onChange={(e) => setApiKey(e.target.value)} autoComplete="off" />
            </Field>
            <Field label="شمارهٔ فرستنده" htmlFor="sms-sender">
              <Input id="sms-sender" dir="ltr" value={sender ?? s!.sender} onChange={(e) => setSender(e.target.value)} />
            </Field>
            <Field label="الگوی کد ورود" htmlFor="sms-otp-template">
              <Input id="sms-otp-template" dir="ltr" value={otpTemplate ?? s!.otp_template} onChange={(e) => setOtpTemplate(e.target.value)} />
            </Field>
            <Field label="امضا" htmlFor="sms-signature" hint="به انتهای هر پیام افزوده می‌شود، حداکثر ۶۰ نویسه">
              <Input id="sms-signature" maxLength={60} value={signature ?? s!.signature} onChange={(e) => setSignature(e.target.value)} />
            </Field>
          </div>
          <div className="flex items-center justify-between rounded-xl border bg-muted/30 px-4 py-3">
            <div>
              <div className="text-sm font-semibold">سرویس پیامک فعال است</div>
              <div className="text-xs text-muted-foreground">خاموش کردن، همهٔ ارسال‌ها را متوقف می‌کند</div>
            </div>
            <Switch checked={s!.enabled} onCheckedChange={toggleEnabled} aria-label="فعال بودن سرویس پیامک" />
          </div>
          <Button onClick={save} disabled={busy} className="w-fit gap-1.5">
            {busy ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
            ذخیرهٔ تنظیمات
          </Button>

          <div className="rounded-xl border border-dashed p-4">
            <div className="mb-2 text-sm font-semibold">ارسال پیام آزمایشی</div>
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input dir="ltr" placeholder="09xxxxxxxxx" value={testTo} onChange={(e) => setTestTo(e.target.value)} className="sm:max-w-56" />
              <Button variant="outline" onClick={sendTest} disabled={testBusy} className="gap-1.5">
                {testBusy && <Loader2 className="size-4 animate-spin" />}
                ارسال آزمایشی
              </Button>
            </div>
            {testResult && (
              <p className={`mt-2 text-xs ${testResult.ok ? "text-success" : "text-destructive"}`}>
                {testResult.ok ? `موفق — از راه ${testResult.via}` : `ناموفق: ${testResult.error}`}
              </p>
            )}
          </div>
        </div>
      )}
    </Section>
  );
}

/* ───────────────────────── service events ───────────────────────── */

function EventsCard() {
  const [stage, setStage] = useState("");
  const [level, setLevel] = useState("");
  const q = useQuery({
    queryKey: ["sms", "events", stage, level],
    queryFn: () => api<{ events: SmsEvent[]; count: number }>(`/sms/events${qs({ stage, level, limit: 100 })}`),
  });
  return (
    <Section title="رویدادهای سرویس پیامک" hint={q.data ? `${faNum(q.data.count)} رویداد` : undefined}>
      <Toolbar className="mb-3">
        <Field label="مرحله" htmlFor="sms-ev-stage" className="w-40">
          <NativeSelect id="sms-ev-stage" value={stage} onChange={(e) => setStage(e.target.value)}>
            <option value="">همه (بدون دریافتی)</option>
            {Object.entries(STAGE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </NativeSelect>
        </Field>
        <Field label="سطح" htmlFor="sms-ev-level" className="w-36">
          <NativeSelect id="sms-ev-level" value={level} onChange={(e) => setLevel(e.target.value)}>
            <option value="">همه</option>
            <option value="info">اطلاع</option>
            <option value="warning">هشدار</option>
            <option value="error">خطا</option>
          </NativeSelect>
        </Field>
      </Toolbar>
      {q.isPending ? (
        <ListSkeleton rows={3} />
      ) : q.isError ? (
        <ErrorNote error={q.error} />
      ) : q.data.events.length === 0 ? (
        <Empty icon={MessageSquareText}>رویدادی ثبت نشده است.</Empty>
      ) : (
        <ul tabIndex={0} aria-label="رویدادهای سرویس پیامک" className="flex max-h-80 flex-col gap-2 overflow-y-auto text-sm">
          {q.data.events.map((e) => (
            <li key={e.id} className="flex items-start gap-2 rounded-lg border px-3 py-2">
              <ToneBadge tone={LEVEL_TONE[e.level] ?? "neutral"}>{STAGE_LABEL[e.stage] ?? e.stage}</ToneBadge>
              <div className="min-w-0 flex-1">
                <div className="truncate">{e.message}</div>
                <div className="text-[11px] text-muted-foreground">
                  {e.at ? faDate(new Date(e.at), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—"}
                  {e.actor ? ` · ${e.actor}` : ""}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

/* ───────────────────────── single send ───────────────────────── */

function SingleSendCard() {
  const [to, setTo] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const { len, parts } = smsParts(message);

  async function send() {
    if (!to.trim() || !message.trim()) {
      toast.error("شماره و متن پیام لازم است");
      return;
    }
    setBusy(true);
    try {
      const res = await api<{ ok: boolean; message_id?: string; error?: string }>("/sms/send", {
        json: { to: parseDigits(to.trim()), message: message.trim() },
      });
      if (res.ok) {
        toast.success("پیامک ارسال شد");
        setTo("");
        setMessage("");
      } else {
        toast.error("ارسال ناموفق بود", res.error);
      }
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "ارسال نشد");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title="ارسال پیام تکی">
      <div className="grid gap-3">
        <Field label="شمارهٔ گیرنده" htmlFor="sms-single-to">
          <Input id="sms-single-to" dir="ltr" placeholder="09xxxxxxxxx" value={to} onChange={(e) => setTo(e.target.value)} className="max-w-56" />
        </Field>
        <Field label="متن پیام" htmlFor="sms-single-body" hint={`${faNum(len)} نویسه · ${faNum(parts)} پیامک`}>
          <Textarea id="sms-single-body" rows={3} value={message} onChange={(e) => setMessage(e.target.value)} maxLength={1000} />
        </Field>
        <Button onClick={send} disabled={busy} className="w-fit gap-1.5">
          {busy ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
          ارسال
        </Button>
      </div>
    </Section>
  );
}

/* ───────────────────────── broadcast ───────────────────────── */

function BroadcastCard({ user }: { user?: User }) {
  const [audience, setAudience] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [campaign, setCampaign] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const allowed = can(user, { roles: ["root", "super_admin"] });

  const q = useQuery({
    queryKey: ["sms", "audiences"],
    queryFn: () => api<{ audiences: Audience[] }>("/sms/audiences"),
    enabled: allowed,
  });
  const chosen = q.data?.audiences.find((a) => a.key === audience);
  const { parts } = smsParts(message);

  async function send() {
    if (!chosen) return;
    setBusy(true);
    try {
      const res = await api<{ ok: boolean; campaign: string; sent: number; failed: number; total: number; error?: string }>(
        "/sms/broadcast",
        { json: { audience: chosen.key, message: message.trim(), campaign: campaign.trim() || undefined, confirm_count: chosen.count } },
      );
      toast.success(`ارسال گروهی «${res.campaign}»`, `${faNum(res.sent)} موفق از ${faNum(res.total)}`);
      setMessage("");
      setCampaign("");
      setAudience(null);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "ارسال نشد");
    } finally {
      setBusy(false);
      setConfirmOpen(false);
    }
  }

  if (!allowed) return null;

  return (
    <Section title="ارسال گروهی" hint="فقط برای مدیر ارشد" action={<ToneBadge tone="primary">مدیر ارشد</ToneBadge>}>
      {q.isPending ? (
        <ListSkeleton rows={3} />
      ) : q.isError ? (
        <ErrorNote error={q.error} />
      ) : (
        <div className="grid gap-4">
          <ul className="grid gap-2 sm:grid-cols-2">
            {q.data.audiences.map((a) => (
              <li key={a.key}>
                <label className={`flex cursor-pointer items-center justify-between gap-2 rounded-xl border px-3 py-2.5 text-sm transition-colors ${audience === a.key ? "border-primary bg-primary/8" : "hover:bg-muted/50"}`}>
                  <span className="flex items-center gap-2">
                    <input type="radio" name="sms-audience" className="accent-primary" checked={audience === a.key} onChange={() => setAudience(a.key)} />
                    {a.label}
                  </span>
                  <span className="tabular text-muted-foreground">{a.count === null ? "—" : faNum(a.count)} نفر</span>
                </label>
              </li>
            ))}
          </ul>
          <Field label="نام کمپین (اختیاری)" htmlFor="sms-campaign">
            <Input id="sms-campaign" value={campaign} onChange={(e) => setCampaign(e.target.value)} className="max-w-64" />
          </Field>
          <Field label="متن پیام" htmlFor="sms-broadcast-body" hint={`${faNum(parts)} پیامک برای هر گیرنده`}>
            <Textarea id="sms-broadcast-body" rows={3} value={message} onChange={(e) => setMessage(e.target.value)} maxLength={1000} />
          </Field>
          <Button
            variant="destructive"
            disabled={!chosen || !chosen.count || !message.trim()}
            onClick={() => setConfirmOpen(true)}
            className="w-fit gap-1.5"
          >
            <Megaphone className="size-4" />
            ارسال به {chosen ? faNum(chosen.count ?? 0) : "—"} نفر
          </Button>
        </div>
      )}

      <RingDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        icon={Megaphone}
        tone="danger"
        title="ارسال گروهی پیامک"
        description={chosen ? `این پیام برای ${faNum(chosen.count ?? 0)} نفر در گروه «${chosen.label}» ارسال می‌شود و برگشت‌پذیر نیست.` : ""}
        footer={
          <>
            <Button variant="destructive" className="w-full gap-1.5" onClick={send} disabled={busy}>
              {busy && <Loader2 className="size-4 animate-spin" />}
              بله، ارسال شود
            </Button>
            <Button variant="ghost" className="w-full" onClick={() => setConfirmOpen(false)}>انصراف</Button>
          </>
        }
      />
    </Section>
  );
}

/* ───────────────────────── history ───────────────────────── */

function HistoryCard() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [refreshing, setRefreshing] = useState(false);
  const limit = 50;

  const q = useQuery({
    queryKey: ["sms", "messages", search, status, page],
    queryFn: () => api<{ total: number; items: SmsMessage[] }>(
      `/sms/messages${qs({ search, status, limit, offset: (page - 1) * limit })}`,
    ),
  });
  const pages = q.data ? Math.max(1, Math.ceil(q.data.total / limit)) : 1;

  async function refreshDelivery() {
    setRefreshing(true);
    try {
      const res = await api<{ ok: boolean; checked: number; updated: number; error?: string }>(
        "/sms/messages/refresh-status",
        { method: "POST" },
      );
      if (res.ok) toast.success("وضعیت تحویل به‌روز شد", `${faNum(res.updated)} از ${faNum(res.checked)} پیام`);
      else toast.error("به‌روزرسانی ناموفق بود", res.error);
      await qc.invalidateQueries({ queryKey: ["sms", "messages"] });
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "به‌روزرسانی نشد");
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <Section
      title="تاریخچهٔ ارسال"
      hint={q.data ? `${faNum(q.data.total)} پیام` : undefined}
      action={
        <Button variant="outline" size="sm" onClick={refreshDelivery} disabled={refreshing} className="gap-1.5">
          {refreshing ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
          بروزرسانی وضعیت
        </Button>
      }
    >
      <Toolbar className="mb-3">
        <Field label="جستجو" htmlFor="sms-hist-search" className="w-52">
          <div className="relative">
            <Search className="pointer-events-none absolute start-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input id="sms-hist-search" className="ps-8" placeholder="شماره یا متن" value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1); }} />
          </div>
        </Field>
        <Field label="وضعیت" htmlFor="sms-hist-status" className="w-36">
          <NativeSelect id="sms-hist-status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
            <option value="">همه</option>
            <option value="sent">ارسال شد</option>
            <option value="failed">ناموفق</option>
          </NativeSelect>
        </Field>
      </Toolbar>

      {q.isPending ? (
        <ListSkeleton rows={6} />
      ) : q.isError ? (
        <ErrorNote error={q.error} />
      ) : q.data.items.length === 0 ? (
        <Empty icon={MessageSquareText}>پیامی یافت نشد.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <Table aria-label="تاریخچهٔ پیامک">
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>گیرنده</TableHead>
                <TableHead>متن</TableHead>
                <TableHead>وضعیت</TableHead>
                <TableHead>تحویل</TableHead>
                <TableHead>کمپین</TableHead>
                <TableHead>زمان</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {q.data.items.map((m) => (
                <TableRow key={m.id}>
                  <TableCell dir="ltr" className="text-end tabular">{m.to_number}</TableCell>
                  <TableCell className="max-w-[240px] truncate">{m.message}</TableCell>
                  <TableCell><ToneBadge tone={MSG_STATUS_TONE[m.status] ?? "neutral"}>{MSG_STATUS_LABEL[m.status] ?? m.status}</ToneBadge></TableCell>
                  <TableCell className="text-muted-foreground">{m.delivery_text ?? "—"}</TableCell>
                  <TableCell className="text-muted-foreground">{m.campaign ?? "—"}</TableCell>
                  <TableCell className="tabular text-muted-foreground">
                    {m.sent_at ? faDate(new Date(m.sent_at), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      <div className="mt-4">
        <Pagination page={page} pages={pages} onPage={setPage} />
      </div>
    </Section>
  );
}

/* ───────────────────────── page ───────────────────────── */

export function SmsView() {
  const user = useSession().data?.user;
  const account = useQuery({ queryKey: ["sms", "account"], queryFn: () => api<SmsAccount>("/sms/account") });
  const stats = useQuery({ queryKey: ["sms", "stats"], queryFn: () => api<SmsStats>("/sms/stats") });
  const isBoss = useMemo(() => can(user, { roles: ["root", "super_admin"] }), [user]);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader icon={MessageSquareText} title="پیامک" hint="ارسال، گروه‌ها و تاریخچهٔ پیامک‌های کاوه‌نگار" />
      <Tiles account={account.data} stats={stats.data} />
      {isBoss && <Reveal><SettingsCard /></Reveal>}
      <div className="grid gap-5 xl:grid-cols-2">
        <Reveal><EventsCard /></Reveal>
        <Reveal delay={0.05}><SingleSendCard /></Reveal>
      </div>
      <Reveal><BroadcastCard user={user} /></Reveal>
      <Reveal><HistoryCard /></Reveal>
    </div>
  );
}
