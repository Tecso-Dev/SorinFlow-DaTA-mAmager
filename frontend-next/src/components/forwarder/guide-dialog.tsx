"use client";

// The six-step install guide (GET /forwarder/devices/{id}/config), built on
// the server so it can never drift from what /scraper/otp-inbound actually
// accepts. Polls while open so a rotate or a SIM edit from another tab shows
// up here without a page reload — the QrShield picks up the new payload on
// its own the next time it is revealed.

import {
  Battery, CheckCheck, Copy, Download, ExternalLink, Loader2, ScanLine, Send, ShieldAlert, Smartphone, TriangleAlert,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ErrorNote, ListSkeleton, RingDialog, ToneBadge } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { Tilt } from "@/components/viz";
import { api, ApiError } from "@/lib/api";
import { faDate, faNum } from "@/lib/format";
import { HEALTH_TONE } from "./shared";
import type { DeviceConfig, DevicesResponse, Health, Rule, TestResult } from "./types";
import { QrShield } from "./qr-shield";

async function copyText(text: string, label: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(`${label} کپی شد`);
  } catch {
    toast.error("کپی نشد", "دسترسی به کلیپ‌بورد رد شد");
  }
}

export function GuideDialog({ id, onOpenChange }: { id: number | null; onOpenChange: (o: boolean) => void }) {
  const open = id !== null;
  const qc = useQueryClient();

  const cfg = useQuery({
    queryKey: ["forwarder", "config", id],
    queryFn: () => api<DeviceConfig>(`/forwarder/devices/${id}/config`),
    enabled: open,
    refetchInterval: open ? 10_000 : false,
  });
  // /config's `device` has no health block (only list/create/rotate attach
  // one) — read the live one from the devices list's own cache, which the
  // page already polls.
  const devicesQ = useQuery({
    queryKey: ["forwarder", "devices"],
    queryFn: () => api<DevicesResponse>("/forwarder/devices"),
    enabled: open,
  });
  const health = devicesQ.data?.devices.find((dv) => dv.id === id)?.health;

  const test = useMutation({
    mutationFn: () => api<TestResult>(`/forwarder/devices/${id}/test`, { method: "POST" }),
    onSuccess: (r) => {
      toast[r.ok ? "success" : "info"](r.ok ? "گوشی وصل است" : "هنوز خبری از گوشی نیست", r.hint_fa);
      qc.invalidateQueries({ queryKey: ["forwarder", "devices"] });
      qc.invalidateQueries({ queryKey: ["forwarder", "config", id] });
    },
    onError: (e) => toast.error("بررسی انجام نشد", e instanceof ApiError ? e.message : undefined),
  });

  const d = cfg.data?.device;

  return (
    <RingDialog
      open={open}
      onOpenChange={onOpenChange}
      icon={Smartphone}
      title={d ? `راهنمای نصب — ${d.label || d.device_id}` : "راهنمای نصب"}
      description="شش قدم، از دانلود برنامه تا رسیدن اولین کد. همین صفحه را باز نگه دارید تا موقع تنظیم برنامه روی گوشی."
      wide
      footer={
        <Button variant="ghost" className="w-full" onClick={() => onOpenChange(false)}>بستن</Button>
      }
    >
      {cfg.isLoading ? (
        <ListSkeleton rows={4} />
      ) : cfg.isError ? (
        <ErrorNote error={cfg.error} />
      ) : cfg.data ? (
        <GuideBody cfg={cfg.data} health={health} onTest={() => test.mutate()} testing={test.isPending} />
      ) : null}
    </RingDialog>
  );
}

function Step({ n, title, warn, children }: { n: number; title: string; warn?: boolean; children: React.ReactNode }) {
  return (
    <li className={warn ? "rounded-xl border border-warning/30 bg-warning/8 p-3" : "rounded-xl border p-3"}>
      <div className="flex items-center gap-2">
        <span
          className={
            warn
              ? "grid size-6 shrink-0 place-items-center rounded-full bg-warning/20 text-xs font-bold text-warning"
              : "grid size-6 shrink-0 place-items-center rounded-full bg-primary/12 text-xs font-bold text-primary"
          }
        >
          {faNum(n)}
        </span>
        <h3 className="text-sm font-bold">{title}</h3>
        {warn && <ShieldAlert className="size-4 text-warning" aria-hidden />}
      </div>
      <div className="mt-2 ms-8 grid gap-2 text-sm leading-7 text-muted-foreground">{children}</div>
    </li>
  );
}

function CopyRow({ label, value, mono = true }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border bg-muted/30 px-2.5 py-1.5">
      <div className="min-w-0 flex-1">
        <div className="text-[11px] text-muted-foreground">{label}</div>
        <div dir="ltr" className={`truncate text-start text-xs ${mono ? "font-mono" : ""}`}>{value}</div>
      </div>
      <Button type="button" variant="ghost" size="icon-sm" aria-label={`کپی ${label}`} onClick={() => copyText(value, label)}>
        <Copy />
      </Button>
    </div>
  );
}

function RuleCard({ rule }: { rule: Rule }) {
  return (
    <div className="rounded-lg border p-2.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-xs font-semibold">{rule.name_fa}</span>
        {rule.sim_slot && <ToneBadge tone="violet">سیم {faNum(rule.sim_slot)}</ToneBadge>}
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground">{rule.why_fa}</p>
      <div className="mt-2 grid grid-cols-2 gap-1.5 text-[11px]">
        <div className="rounded-md bg-muted/40 px-2 py-1">
          <span className="text-muted-foreground">فرستنده: </span>
          <span dir="ltr" className="font-mono">{rule.sender}</span>
        </div>
        <div className="rounded-md bg-muted/40 px-2 py-1 truncate">
          <span className="text-muted-foreground">فیلتر متن: </span>
          <span>{rule.text_filter}</span>
        </div>
      </div>
      <div className="mt-1.5 flex items-start gap-1.5">
        <pre dir="ltr" className="max-h-24 flex-1 overflow-auto rounded-md bg-muted/50 p-2 text-start text-[10px] leading-5 whitespace-pre-wrap">{rule.template}</pre>
        <Button type="button" variant="ghost" size="icon-sm" aria-label={`کپی الگوی «${rule.name_fa}»`} onClick={() => copyText(rule.template, "الگوی JSON")}>
          <Copy />
        </Button>
      </div>
    </div>
  );
}

function GuideBody({
  cfg, health, onTest, testing,
}: { cfg: DeviceConfig; health?: Health; onTest: () => void; testing: boolean }) {
  const [tested, setTested] = useState(false);
  const d = cfg.device;
  const headersJson = JSON.stringify(cfg.headers, null, 2);

  return (
    <div className="grid gap-4">
      <Tilt className="rounded-xl border bg-linear-to-br from-indigo-500/10 to-violet-600/10 p-3" max={4}>
        <div className="flex items-center gap-3">
          <div className="relative size-11 shrink-0" aria-hidden>
            {[3, 2, 1].map((k) => (
              <div key={k} className="absolute inset-0 rounded-xl bg-violet-900/60" style={{ transform: `translate(${k}px, ${k}px)`, opacity: 0.3 + (3 - k) * 0.15 }} />
            ))}
            <div className="absolute inset-0 grid place-items-center rounded-xl bg-linear-to-br from-indigo-400 to-violet-600">
              <Smartphone className="size-5 text-white" />
            </div>
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-bold">{d.label || d.device_id}</div>
            <div dir="ltr" className="text-[11px] text-muted-foreground">{cfg.accounts.join(" · ") || "—"}</div>
          </div>
          {health && <ToneBadge tone={HEALTH_TONE[health.state]}>{health.message_fa}</ToneBadge>}
        </div>
      </Tilt>

      <ol className="grid gap-3">
        <Step n={1} title="دانلود برنامه">
          <p>
            برنامه در گوگل‌پلی نیست — چون تمام پیامک‌های گوشی را می‌خواند، پلی چنین برنامه‌ای را نمی‌پذیرد. هنگام نصب،
            «Play Protect» احتمالاً هشدار می‌دهد؛ نصب را ادامه دهید.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button asChild size="sm">
              <a href={cfg.android_apk_url} target="_blank" rel="noopener noreferrer">
                <Download /> دانلود از سایت{cfg.android_apk_version ? ` (نسخهٔ ${cfg.android_apk_version})` : ""}
              </a>
            </Button>
            <Button asChild size="sm" variant="outline">
              <a href={cfg.android_release_url} target="_blank" rel="noopener noreferrer">
                <ExternalLink /> دانلود از گیت‌هاب
              </a>
            </Button>
          </div>
        </Step>

        <Step n={2} title="مجوز خواندن پیامک">
          <p>هنگام باز کردن برنامه، دسترسی «خواندن پیامک» را بدهید.</p>
          <p>
            <strong className="text-foreground">اندروید ۱۳ به بالا:</strong> اگر دکمهٔ اجازه غیرفعال بود، از تنظیمات برنامه
            سه‌نقطه بالا سمت راست → «Allow restricted settings» را بزنید، بعد دوباره مجوز را بدهید.
          </p>
        </Step>

        <Step n={3} title="غیرفعال‌کردن محدودیت باتری و Autostart" warn>
          <p className="font-medium text-foreground">
            مهم‌ترین قدم: اگر این را نزنید، اندروید بعد از چند ساعت برنامه را در پس‌زمینه می‌کشد و کدها دیگر نمی‌رسند.
          </p>
          <p>در تنظیمات باتری گوشی، برای این برنامه «بدون محدودیت / Unrestricted» را انتخاب کنید و در تنظیمات Autostart (اگر گوشی دارد) روشنش کنید.</p>
        </Step>

        <Step n={4} title="کد QR و فیلدهای دستی">
          <p>در برنامه، گزینهٔ اسکن QR را بزنید و این کد را نشان دهید — تنظیمات کامل یک‌جا وارد می‌شود:</p>
          <div className="not-prose flex flex-col items-center py-1">
            <QrShield payload={cfg.setup_payload} />
          </div>
          <p>اگر برنامهٔ عمومی «SMS Forwarder» استفاده می‌کنید، این فیلدها را دستی وارد کنید:</p>
          <div className="grid gap-1.5">
            <CopyRow label="فرستنده" value="*" />
            <CopyRow label="آدرس Webhook" value={cfg.endpoints.inbound} />
          </div>
          <details className="rounded-lg border p-2.5">
            <summary className="cursor-pointer text-xs font-semibold text-foreground">هدرها (JSON)</summary>
            <div className="mt-1.5 flex items-start gap-1.5 rounded-md border border-destructive/30 bg-destructive/8 p-2 text-[11px] text-destructive">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              رمز دستگاه در همین هدرهاست — با کسی به‌اشتراک نگذارید.
            </div>
            <div className="mt-1.5 flex items-start gap-1.5">
              <pre dir="ltr" className="max-h-32 flex-1 overflow-auto rounded-md bg-muted/50 p-2 text-start text-[10px] leading-5">{headersJson}</pre>
              <Button type="button" variant="ghost" size="icon-sm" aria-label="کپی هدرها" onClick={() => copyText(headersJson, "هدرها")}>
                <Copy />
              </Button>
            </div>
          </details>
          <p className="text-[11px]">قالب JSON Payload هر قانون، در قدم ۶ همین صفحه کنار خودش است.</p>
        </Step>

        <Step n={5} title="آزمایش اتصال">
          <p>در خود برنامه، دکمهٔ «Send test to server» یا «TEST» را بزنید؛ بعد اینجا بررسی کنید:</p>
          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" size="sm" variant="outline" disabled={testing} onClick={() => { onTest(); setTested(true); }}>
              {testing ? <Loader2 className="animate-spin" /> : <ScanLine />} بررسی اتصال
            </Button>
            {tested && <CheckCheck className="size-4 text-success" aria-hidden />}
          </div>
          <p className="text-[11px]">
            این یک ping زنده به گوشی نیست — چیزی نمی‌تواند از راه دور گوشی را وادار به پاسخ کند. فقط می‌گوید آخرین باری که
            خودِ گوشی خبر داد، چه زمانی بوده.
          </p>
          {d.last_seen_at && (
            <p className="text-[11px]">
              <Battery className="me-1 inline size-3" aria-hidden />
              آخرین خبر: {faDate(new Date(d.last_seen_at), { dateStyle: "short", timeStyle: "short" })}
              {d.battery !== null && ` · باتری ${faNum(d.battery)}٪`}
            </p>
          )}
        </Step>

        <Step n={6} title="قانون‌های تشخیص کد">
          <p>این‌ها همان دو قانونی‌اند که کد QR وارد می‌کند — اگر دستی وارد می‌کنید، عیناً همین‌ها را بسازید:</p>
          <div className="grid gap-2 sm:grid-cols-2">
            {cfg.rules.map((r) => <RuleCard key={r.name_fa} rule={r} />)}
          </div>
          {cfg.rules_sim2.length > 0 && (
            <>
              <p className="mt-1 flex items-center gap-1.5 text-[11px] font-semibold text-foreground">
                <Send className="size-3.5" aria-hidden /> گوشی دو سیم‌کارته — دو قانون اضافه برای سیم دوم:
              </p>
              <div className="grid gap-2 sm:grid-cols-2">
                {cfg.rules_sim2.map((r) => <RuleCard key={r.name_fa} rule={r} />)}
              </div>
            </>
          )}
        </Step>
      </ol>

      {!cfg.ios.available && (
        <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <ExternalLink className="size-3.5" aria-hidden /> {cfg.ios.message_fa}
        </p>
      )}
    </div>
  );
}
