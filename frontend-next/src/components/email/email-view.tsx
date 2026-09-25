"use client";

// ایمیل — SMTP settings, template preview, campaigns and delivery history.
// Mirrors frontend/js/app.js loadEmail() and friends against
// app/api/routes/email.py.

import {
  Download, Eye, Loader2, Mail, Megaphone, Plug, Save, Send, ShieldCheck,
} from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
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
import { faDate, faNum } from "@/lib/format";
import { useSession, can } from "@/lib/session";

/* ───────────────────────── types (app/api/routes/email.py) ───────────────────────── */

type EmailSettings = {
  host: string; port: number; user: string; password_masked: string; configured: boolean;
  password_source: "env" | "panel" | null; from_name: string; reply_to: string; from_email: string;
  security: "starttls" | "ssl" | "none"; enabled: boolean;
};
type EmailStats = { total: number; last_30_days: number; failed_30_days: number; login_codes_30_days: number; success_rate: number | null };
type Template = { key: string; label: string };
type Audience = { key: string; label: string; count: number | null };
type EmailMessage = {
  id: number; to_email: string; subject: string | null; template: string | null; status: string;
  error: string | null; message_id: string | null; sent_by: string | null; created_at: string | null;
};

const TEMPLATE_LABEL: Record<string, string> = {
  broadcast: "کمپین", login_code: "کد ورود", notification: "اطلاع‌رسانی",
};
const STATUS_TONE: Record<string, "success" | "danger"> = { sent: "success", failed: "danger" };

/* ───────────────────────── tiles ───────────────────────── */

function Tiles({ settings, stats }: { settings?: EmailSettings; stats?: EmailStats }) {
  const items = [
    {
      key: "conn", label: "وضعیت اتصال", icon: Plug, value: null,
      text: settings ? (settings.configured ? "متصل" : "بدون تنظیم") : "—",
      tint: settings?.configured ? "text-success bg-success/12" : "text-warning bg-warning/12",
    },
    { key: "sent", label: "ارسال‌شده در ۳۰ روز", icon: Send, value: stats?.last_30_days ?? null, tint: "text-info bg-info/12" },
    { key: "codes", label: "کد ورود ارسالی", icon: ShieldCheck, value: stats?.login_codes_30_days ?? null, tint: "text-primary bg-primary/12" },
    { key: "failed", label: "ناموفق در ۳۰ روز", icon: Mail, value: stats?.failed_30_days ?? null, tint: "text-destructive bg-destructive/12" },
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
                {it.value !== null ? <CountUp value={it.value} /> : it.text}
              </div>
            </div>
          </Tilt>
        </Reveal>
      ))}
    </div>
  );
}

/* ───────────────────────── SMTP settings (super_admin) ───────────────────────── */

function SettingsCard() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["email", "settings"], queryFn: () => api<EmailSettings>("/email/settings") });
  const s = q.data;
  const [password, setPassword] = useState("");
  const [fields, setFields] = useState<Partial<Record<keyof EmailSettings, string>>>({});
  const [busy, setBusy] = useState(false);
  const [verifyBusy, setVerifyBusy] = useState(false);
  const [testTo, setTestTo] = useState("");
  const [testBusy, setTestBusy] = useState(false);

  const val = (k: "host" | "user" | "from_name" | "reply_to" | "from_email") => fields[k] ?? s?.[k] ?? "";
  const host = val("host");
  const isGmail = /gmail/i.test(host);

  async function save() {
    setBusy(true);
    try {
      const body: Record<string, unknown> = { ...fields };
      if (password.trim()) body.password = password.trim();
      if (fields.port !== undefined) body.port = Number(fields.port);
      await api("/email/settings", { method: "PUT", json: body });
      await qc.invalidateQueries({ queryKey: ["email", "settings"] });
      setPassword("");
      setFields({});
      toast.success("تنظیمات ایمیل ذخیره شد");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "ذخیره نشد");
    } finally {
      setBusy(false);
    }
  }

  async function verify() {
    setVerifyBusy(true);
    try {
      const res = await api<{ ok?: boolean; success?: boolean; error?: string; detail?: string }>("/email/verify", { method: "POST" });
      const ok = res.ok ?? res.success ?? true;
      if (ok) toast.success("اتصال برقرار شد");
      else toast.error("اتصال ناموفق بود", res.error ?? res.detail);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "بررسی نشد");
    } finally {
      setVerifyBusy(false);
    }
  }

  async function sendTest() {
    if (!testTo.trim()) {
      toast.error("آدرس گیرنده را وارد کنید");
      return;
    }
    setTestBusy(true);
    try {
      const res = await api<{ ok: boolean; error?: string }>(`/email/test${qs({ to: testTo.trim() })}`, { method: "POST" });
      if (res.ok) toast.success("ایمیل آزمایشی ارسال شد");
      else toast.error("ارسال ناموفق بود", res.error);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "ارسال نشد");
    } finally {
      setTestBusy(false);
    }
  }

  return (
    <Section
      title="تنظیمات ایمیل (SMTP)"
      action={s && <ToneBadge tone={s.configured ? "success" : "warning"}>{s.configured ? "پیکربندی‌شده" : "بدون تنظیم"}</ToneBadge>}
    >
      {q.isPending ? (
        <ListSkeleton rows={3} />
      ) : q.isError ? (
        <ErrorNote error={q.error} />
      ) : (
        <div className="grid gap-4">
          {isGmail && (
            <div className="rounded-xl border border-info/30 bg-info/8 px-4 py-3 text-xs text-info">
              با جیمیل، به‌جای رمز عبور معمولی از «رمز برنامه» (App Password) گوگل استفاده کنید.
            </div>
          )}
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="هاست" htmlFor="em-host">
              <Input id="em-host" dir="ltr" value={host} onChange={(e) => setFields((f) => ({ ...f, host: e.target.value }))} />
            </Field>
            <Field label="پورت" htmlFor="em-port">
              <Input id="em-port" dir="ltr" inputMode="numeric" value={fields.port ?? String(s!.port)} onChange={(e) => setFields((f) => ({ ...f, port: e.target.value }))} />
            </Field>
            <Field label="کاربر" htmlFor="em-user">
              <Input id="em-user" dir="ltr" value={val("user")} onChange={(e) => setFields((f) => ({ ...f, user: e.target.value }))} />
            </Field>
            <Field label="رمز عبور" htmlFor="em-pass" hint={`فعلی: ${s!.password_masked || "—"}`}>
              <Input id="em-pass" dir="ltr" type="password" placeholder="برای تغییر وارد کنید" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="off" />
            </Field>
            <Field label="رمزگذاری" htmlFor="em-sec">
              <NativeSelect id="em-sec" value={fields.security ?? s!.security} onChange={(e) => setFields((f) => ({ ...f, security: e.target.value }))}>
                <option value="starttls">STARTTLS</option>
                <option value="ssl">SSL</option>
                <option value="none">بدون رمزگذاری</option>
              </NativeSelect>
            </Field>
            <Field label="نام فرستنده" htmlFor="em-fname">
              <Input id="em-fname" value={val("from_name")} onChange={(e) => setFields((f) => ({ ...f, from_name: e.target.value }))} />
            </Field>
            <Field label="ایمیل فرستنده (اختیاری)" htmlFor="em-femail">
              <Input id="em-femail" dir="ltr" value={val("from_email")} onChange={(e) => setFields((f) => ({ ...f, from_email: e.target.value }))} />
            </Field>
            <Field label="پاسخ به" htmlFor="em-reply">
              <Input id="em-reply" dir="ltr" value={val("reply_to")} onChange={(e) => setFields((f) => ({ ...f, reply_to: e.target.value }))} />
            </Field>
          </div>
          <div className="flex items-center justify-between rounded-xl border bg-muted/30 px-4 py-3">
            <div>
              <div className="text-sm font-semibold">سرویس ایمیل فعال است</div>
              <div className="text-xs text-muted-foreground">خاموش کردن، همهٔ ارسال‌ها را متوقف می‌کند</div>
            </div>
            <Switch
              checked={fields.enabled !== undefined ? fields.enabled === "true" : s!.enabled}
              onCheckedChange={async (v) => {
                try {
                  await api("/email/settings", { method: "PUT", json: { enabled: v } });
                  await qc.invalidateQueries({ queryKey: ["email", "settings"] });
                } catch (err) {
                  toast.error(err instanceof ApiError ? err.message : "ذخیره نشد");
                }
              }}
              aria-label="فعال بودن سرویس ایمیل"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={save} disabled={busy} className="gap-1.5">
              {busy ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
              ذخیرهٔ تنظیمات
            </Button>
            <Button variant="outline" onClick={verify} disabled={verifyBusy} className="gap-1.5">
              {verifyBusy ? <Loader2 className="size-4 animate-spin" /> : <Plug className="size-4" />}
              بررسی اتصال
            </Button>
          </div>
          <div className="rounded-xl border border-dashed p-4">
            <div className="mb-2 text-sm font-semibold">ارسال ایمیل آزمایشی</div>
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input dir="ltr" placeholder="you@example.com" value={testTo} onChange={(e) => setTestTo(e.target.value)} className="sm:max-w-64" />
              <Button variant="outline" onClick={sendTest} disabled={testBusy} className="gap-1.5">
                {testBusy && <Loader2 className="size-4 animate-spin" />}
                ارسال آزمایشی
              </Button>
            </div>
          </div>
        </div>
      )}
    </Section>
  );
}

/* ───────────────────────── free send (super_admin) ───────────────────────── */

function SendCard() {
  const [to, setTo] = useState("");
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function send() {
    if (!to.trim() || !subject.trim() || !message.trim()) {
      toast.error("گیرنده، موضوع و متن لازم است");
      return;
    }
    setBusy(true);
    try {
      const res = await api<{ ok: boolean; error?: string }>("/email/send", {
        json: { to: to.trim(), subject: subject.trim(), message: message.trim() },
      });
      if (res.ok) {
        toast.success("ایمیل ارسال شد");
        setTo(""); setSubject(""); setMessage("");
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
    <Section title="ارسال ایمیل">
      <div className="grid gap-3">
        <Field label="گیرنده" htmlFor="em-send-to">
          <Input id="em-send-to" dir="ltr" value={to} onChange={(e) => setTo(e.target.value)} className="max-w-72" />
        </Field>
        <Field label="موضوع" htmlFor="em-send-subject">
          <Input id="em-send-subject" value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={200} />
        </Field>
        <Field label="متن" htmlFor="em-send-body">
          <Textarea id="em-send-body" rows={4} value={message} onChange={(e) => setMessage(e.target.value)} maxLength={4000} />
        </Field>
        <Button onClick={send} disabled={busy} className="w-fit gap-1.5">
          {busy ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
          ارسال
        </Button>
      </div>
    </Section>
  );
}

/* ───────────────────────── template preview ───────────────────────── */

function TemplatePreviewCard() {
  const q = useQuery({ queryKey: ["email", "templates"], queryFn: () => api<{ templates: Template[] }>("/email/templates") });
  const [name, setName] = useState<string | null>(null);
  const active = name ?? q.data?.templates[0]?.key;

  return (
    <Section title="پیش‌نمایش قالب‌ها">
      {q.isPending ? (
        <ListSkeleton rows={2} />
      ) : q.isError ? (
        <ErrorNote error={q.error} />
      ) : (
        <div className="grid gap-3">
          <Field label="قالب" htmlFor="em-tpl-select" className="max-w-64">
            <NativeSelect id="em-tpl-select" value={active} onChange={(e) => setName(e.target.value)}>
              {q.data.templates.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
            </NativeSelect>
          </Field>
          {active && (
            <iframe
              key={active}
              title="پیش‌نمایش قالب ایمیل"
              src={`/api/email/preview/${active}`}
              sandbox="allow-same-origin"
              className="h-[420px] w-full rounded-xl border bg-white"
            />
          )}
        </div>
      )}
    </Section>
  );
}

/* ───────────────────────── campaign (super_admin) ───────────────────────── */

function CampaignCard() {
  const [audience, setAudience] = useState<string | null>(null);
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [ctaLabel, setCtaLabel] = useState("");
  const [ctaUrl, setCtaUrl] = useState("");
  const [previewHtml, setPreviewHtml] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  const q = useQuery({ queryKey: ["email", "audiences"], queryFn: () => api<{ audiences: Audience[] }>("/email/audiences") });
  const chosen = q.data?.audiences.find((a) => a.key === audience);

  useEffect(() => {
    const t = setTimeout(() => {
      api<string>("/email/broadcast/preview", { json: { subject, message, cta_label: ctaLabel || undefined, cta_url: ctaUrl || undefined } })
        .then((html) => setPreviewHtml(html))
        .catch(() => {});
    }, 400);
    return () => clearTimeout(t);
  }, [subject, message, ctaLabel, ctaUrl]);

  async function send() {
    if (!chosen) return;
    setBusy(true);
    try {
      const res = await api<{ ok: boolean; sent: number; failed: number; total: number }>("/email/broadcast", {
        json: { audience: chosen.key, subject: subject.trim(), message: message.trim(), cta_label: ctaLabel || undefined, cta_url: ctaUrl || undefined, confirm_count: chosen.count },
      });
      toast.success("کمپین ارسال شد", `${faNum(res.sent)} موفق از ${faNum(res.total)}`);
      setSubject(""); setMessage(""); setCtaLabel(""); setCtaUrl(""); setAudience(null);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "ارسال نشد");
    } finally {
      setBusy(false);
      setConfirmOpen(false);
    }
  }

  async function exportAudience() {
    if (!chosen) return;
    try {
      const res = await api<{ audience: string; label: string; count: number; emails: string[] }>(`/email/export${qs({ audience: chosen.key })}`);
      const blob = new Blob([res.emails.join("\n")], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `emails-${chosen.key}.txt`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "دریافت نشد");
    }
  }

  return (
    <Section title="کمپین ایمیلی" hint="فقط برای مدیر ارشد" action={<ToneBadge tone="primary">مدیر ارشد</ToneBadge>}>
      {q.isPending ? (
        <ListSkeleton rows={3} />
      ) : q.isError ? (
        <ErrorNote error={q.error} />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="grid gap-3">
            <ul className="grid gap-2">
              {q.data.audiences.map((a) => (
                <li key={a.key}>
                  <label className={`flex cursor-pointer items-center justify-between gap-2 rounded-xl border px-3 py-2.5 text-sm transition-colors ${audience === a.key ? "border-primary bg-primary/8" : "hover:bg-muted/50"}`}>
                    <span className="flex items-center gap-2">
                      <input type="radio" name="email-audience" className="accent-primary" checked={audience === a.key} onChange={() => setAudience(a.key)} />
                      {a.label}
                    </span>
                    <span className="tabular text-muted-foreground">{a.count === null ? "—" : faNum(a.count)} نفر</span>
                  </label>
                </li>
              ))}
            </ul>
            {chosen && (
              <Button variant="outline" size="sm" className="w-fit gap-1.5" onClick={exportAudience}>
                <Download className="size-3.5" /> دریافت فهرست {chosen.label}
              </Button>
            )}
            <Field label="موضوع" htmlFor="em-camp-subject">
              <Input id="em-camp-subject" value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={200} />
            </Field>
            <Field label="متن" htmlFor="em-camp-body">
              <Textarea id="em-camp-body" rows={4} value={message} onChange={(e) => setMessage(e.target.value)} maxLength={4000} />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="برچسب دکمه (اختیاری)" htmlFor="em-camp-cta-label">
                <Input id="em-camp-cta-label" value={ctaLabel} onChange={(e) => setCtaLabel(e.target.value)} />
              </Field>
              <Field label="آدرس دکمه (اختیاری)" htmlFor="em-camp-cta-url">
                <Input id="em-camp-cta-url" dir="ltr" value={ctaUrl} onChange={(e) => setCtaUrl(e.target.value)} />
              </Field>
            </div>
            <Button
              variant="destructive"
              disabled={!chosen || !chosen.count || !subject.trim() || !message.trim()}
              onClick={() => setConfirmOpen(true)}
              className="w-fit gap-1.5"
            >
              <Megaphone className="size-4" />
              ارسال به {chosen ? faNum(chosen.count ?? 0) : "—"} نفر
            </Button>
          </div>
          <div>
            <div className="mb-1.5 flex items-center gap-1.5 text-xs text-muted-foreground"><Eye className="size-3.5" /> پیش‌نمایش زنده</div>
            <iframe title="پیش‌نمایش کمپین" srcDoc={previewHtml} sandbox="allow-same-origin" className="h-[420px] w-full rounded-xl border bg-white" />
          </div>
        </div>
      )}

      <RingDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        icon={Megaphone}
        tone="danger"
        title="ارسال کمپین ایمیلی"
        description={chosen ? `این ایمیل برای ${faNum(chosen.count ?? 0)} نفر در گروه «${chosen.label}» ارسال می‌شود و برگشت‌پذیر نیست.` : ""}
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
  const [template, setTemplate] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const limit = 50;
  const q = useQuery({
    queryKey: ["email", "messages", template, status, page],
    queryFn: () => api<{ total: number; items: EmailMessage[] }>(`/email/messages${qs({ template, status, limit, offset: (page - 1) * limit })}`),
  });
  const pages = q.data ? Math.max(1, Math.ceil(q.data.total / limit)) : 1;

  return (
    <Section title="تاریخچهٔ ارسال" hint={q.data ? `${faNum(q.data.total)} ایمیل` : undefined}>
      <Toolbar className="mb-3">
        <Field label="قالب" htmlFor="em-hist-tpl" className="w-40">
          <NativeSelect id="em-hist-tpl" value={template} onChange={(e) => { setTemplate(e.target.value); setPage(1); }}>
            <option value="">همه</option>
            {Object.entries(TEMPLATE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </NativeSelect>
        </Field>
        <Field label="وضعیت" htmlFor="em-hist-status" className="w-36">
          <NativeSelect id="em-hist-status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
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
        <Empty icon={Mail}>ایمیلی یافت نشد.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <Table aria-label="تاریخچهٔ ایمیل">
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>گیرنده</TableHead>
                <TableHead>موضوع</TableHead>
                <TableHead>قالب</TableHead>
                <TableHead>وضعیت</TableHead>
                <TableHead>زمان</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {q.data.items.map((m) => (
                <TableRow key={m.id}>
                  <TableCell dir="ltr" className="text-end">{m.to_email}</TableCell>
                  <TableCell className="max-w-[220px] truncate">{m.subject ?? "—"}</TableCell>
                  <TableCell className="text-muted-foreground">{TEMPLATE_LABEL[m.template ?? ""] ?? m.template ?? "—"}</TableCell>
                  <TableCell><ToneBadge tone={STATUS_TONE[m.status] ?? "neutral"}>{m.status === "sent" ? "ارسال شد" : "ناموفق"}</ToneBadge></TableCell>
                  <TableCell className="tabular text-muted-foreground">
                    {m.created_at ? faDate(new Date(m.created_at), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      <div className="mt-4"><Pagination page={page} pages={pages} onPage={setPage} /></div>
    </Section>
  );
}

/* ───────────────────────── page ───────────────────────── */

export function EmailView() {
  const user = useSession().data?.user;
  const settings = useQuery({ queryKey: ["email", "settings-tile"], queryFn: () => api<EmailSettings>("/email/settings"), enabled: can(user, { roles: ["root", "super_admin"] }) });
  const stats = useQuery({ queryKey: ["email", "stats"], queryFn: () => api<EmailStats>("/email/stats") });
  const isBoss = can(user, { roles: ["root", "super_admin"] });

  return (
    <div className="flex flex-col gap-5">
      <PageHeader icon={Mail} title="ایمیل" hint="SMTP، قالب‌ها، کمپین و تاریخچهٔ ارسال" />
      <Tiles settings={settings.data} stats={stats.data} />
      {isBoss && <Reveal><SettingsCard /></Reveal>}
      {isBoss && <Reveal><SendCard /></Reveal>}
      <Reveal><TemplatePreviewCard /></Reveal>
      {isBoss && <Reveal><CampaignCard /></Reveal>}
      <Reveal><HistoryCard /></Reveal>
    </div>
  );
}
