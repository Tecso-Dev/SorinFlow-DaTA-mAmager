"use client";

// Sign-in / sign-up / verify-code, all in one client component — the state
// machine mirrors the old frontend/js/portal.js (switchTab / startVerify /
// startResendCountdown) but through React state instead of DOM classes.

import { Eye, EyeOff, KeyRound, Loader2, Mail, ShieldCheck, TriangleAlert, UserRound } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, ApiError } from "@/lib/api";
import { parseDigits } from "@/lib/format";
import { SESSION_KEY, type Session } from "@/lib/session";

const PHONE_RE = /^09\d{9}$/;
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$/;

type PendingReply = {
  pending: true;
  channel: "sms" | "email";
  ttl: number;
  cooldown: number;
  message: string;
  phone?: string | null;
  debug_code?: string | null;
};

type Step =
  | { kind: "auth"; tab: "login" | "register" }
  | { kind: "verify"; phone: string; password: string; hint: string; cooldown: number; debugCode?: string | null };

function pwScore(v: string) {
  return {
    len: v.length >= 8,
    lower: /[a-z]/.test(v),
    digit: /[0-9]/.test(v),
    upper: /[A-Z]/.test(v) || /[^A-Za-z0-9]/.test(v),
  };
}

const STRENGTH = [
  { w: 0, cls: "bg-transparent", label: "قدرت رمز عبور" },
  { w: 25, cls: "[&>div]:bg-destructive", label: "خیلی ضعیف" },
  { w: 50, cls: "[&>div]:bg-warning", label: "ضعیف" },
  { w: 75, cls: "[&>div]:bg-warning", label: "متوسط" },
  { w: 100, cls: "[&>div]:bg-success", label: "قوی" },
] as const;

const RULES: { key: keyof ReturnType<typeof pwScore>; label: string }[] = [
  { key: "len", label: "حداقل ۸ کاراکتر" },
  { key: "lower", label: "حرف کوچک انگلیسی" },
  { key: "digit", label: "حداقل یک رقم" },
  { key: "upper", label: "حرف بزرگ یا نماد" },
];

/** A field with an inline blur-validated error, matching frontend/js/portal.js. */
function AuthField({
  id, label, error, children,
}: { id: string; label: string; error?: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1.5 text-start">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

function PasswordInput({
  id, value, onChange, autoComplete, onCapsLock,
}: {
  id: string; value: string; onChange: (v: string) => void; autoComplete: string; onCapsLock: (v: boolean) => void;
}) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <Input
        id={id}
        dir="ltr"
        type={show ? "text" : "password"}
        autoComplete={autoComplete}
        required
        maxLength={128}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyUp={(e) => onCapsLock(e.getModifierState("CapsLock"))}
        onKeyDown={(e) => onCapsLock(e.getModifierState("CapsLock"))}
        onBlur={() => onCapsLock(false)}
        className="h-11 pe-11 text-left"
      />
      <button
        type="button"
        onClick={() => setShow((v) => !v)}
        className="absolute inset-y-0 end-0 grid w-11 place-items-center text-muted-foreground hover:text-foreground"
        aria-label={show ? "پنهان کردن رمز" : "نمایش رمز"}
      >
        {show ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
      </button>
    </div>
  );
}

export function AuthView() {
  const router = useRouter();
  const qc = useQueryClient();

  const [step, setStep] = useState<Step>({ kind: "auth", tab: "login" });
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [infoMessage, setInfoMessage] = useState<string | null>(null);

  // login
  const [liId, setLiId] = useState("");
  const [liPw, setLiPw] = useState("");
  const [liCaps, setLiCaps] = useState(false);
  const [remember, setRemember] = useState(true);

  // register
  const [rgName, setRgName] = useState("");
  const [rgPhone, setRgPhone] = useState("");
  const [rgEmail, setRgEmail] = useState("");
  const [rgPw, setRgPw] = useState("");
  const [rgCaps, setRgCaps] = useState(false);
  const [rgOptIn, setRgOptIn] = useState(false);
  const [rgErrors, setRgErrors] = useState<Record<string, string>>({});

  // verify
  const [code, setCode] = useState("");
  const [cooldownLeft, setCooldownLeft] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => () => {
    if (timerRef.current) clearInterval(timerRef.current);
  }, []);

  function startCooldown(seconds: number) {
    if (timerRef.current) clearInterval(timerRef.current);
    setCooldownLeft(seconds);
    timerRef.current = setInterval(() => {
      setCooldownLeft((v) => {
        if (v <= 1) {
          if (timerRef.current) clearInterval(timerRef.current);
          return 0;
        }
        return v - 1;
      });
    }, 1000);
  }

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setFormError(null);
    try {
      await fn();
    } catch (e) {
      setFormError(e instanceof ApiError ? e.message : "خطای ناشناخته؛ دوباره امتحان کنید.");
    } finally {
      setBusy(false);
    }
  }

  function goVerify(phone: string, password: string, reply: PendingReply) {
    setStep({ kind: "verify", phone, password, hint: reply.message, cooldown: reply.cooldown, debugCode: reply.debug_code });
    setCode("");
    startCooldown(reply.cooldown || 90);
  }

  /** Established the account is verified: sign into the portal's own cookie
   *  session (never a bearer token in the browser) and hand the panel-style
   *  session cache what useSession() expects, so the next page renders with
   *  no extra round trip. */
  async function establishSession(phone: string, password: string) {
    const r = await api<Session>("/public/auth/session/login", { json: { identifier: phone, password, remember: true } });
    qc.setQueryData(SESSION_KEY, r);
    router.replace("/portal/me");
  }

  const submitLogin = (e: React.FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const r = await api<Session | PendingReply>("/public/auth/session/login", {
        json: { identifier: liId.trim(), password: liPw, remember },
      });
      if ("pending" in r && r.pending) {
        const phone = r.phone && PHONE_RE.test(r.phone) ? r.phone : liId.trim();
        goVerify(phone, liPw, r);
        setInfoMessage(r.message);
        return;
      }
      qc.setQueryData(SESSION_KEY, r as Session);
      router.replace("/portal/me");
    });
  };

  function validateRegister() {
    const errs: Record<string, string> = {};
    if (rgName.trim().length < 2) errs.name = "نام خود را وارد کنید";
    if (!PHONE_RE.test(rgPhone)) errs.phone = "شماره باید با فرمت ۰۹۱۲۳۴۵۶۷۸۹ باشد";
    const score = pwScore(rgPw);
    if (!score.len) errs.password = "رمز عبور باید حداقل ۸ کاراکتر باشد";
    else if (Object.values(score).filter(Boolean).length < 3) errs.password = "رمز عبور ضعیف است — شرط‌های زیر را کامل کنید";
    if (!EMAIL_RE.test(rgEmail)) errs.email = "ایمیل معتبر وارد کنید — کد ورود به این آدرس فرستاده می‌شود";
    setRgErrors(errs);
    return errs;
  }

  const submitRegister = (e: React.FormEvent) => {
    e.preventDefault();
    const errs = validateRegister();
    if (Object.keys(errs).length) return;
    void run(async () => {
      const r = await api<PendingReply>("/public/auth/register", {
        json: { full_name: rgName.trim(), phone: rgPhone, password: rgPw, email: rgEmail.trim(), marketing_opt_in: rgOptIn },
      });
      goVerify(rgPhone, rgPw, r);
    });
  };

  const submitVerify = (e: React.FormEvent) => {
    e.preventDefault();
    if (step.kind !== "verify" || !code) return;
    void run(async () => {
      await api("/public/auth/verify", { json: { phone: step.phone, code } });
      if (timerRef.current) clearInterval(timerRef.current);
      await establishSession(step.phone, step.password);
    });
  };

  const resend = () => {
    if (step.kind !== "verify") return;
    void run(async () => {
      const r = await api<PendingReply>("/public/auth/resend", { json: { phone: step.phone } });
      setInfoMessage(r.debug_code ? `کد تست: ${r.debug_code}` : r.message || "کد دوباره ارسال شد");
      setStep({ ...step, debugCode: r.debug_code });
      startCooldown(r.cooldown || 90);
    });
  };

  const backToAuth = () => {
    setFormError(null);
    setInfoMessage(null);
    if (timerRef.current) clearInterval(timerRef.current);
    setStep({ kind: "auth", tab: "login" });
  };

  if (step.kind === "verify") {
    return (
      <div className="w-full max-w-[400px]">
        <div className="mb-7 flex flex-col items-center text-center">
          <div className="relative mb-4 grid size-[62px] place-items-center rounded-full bg-linear-to-br from-indigo-500 to-violet-600 shadow-[0_0_40px_-6px_rgb(99_102_241/0.8)]">
            <div className="absolute inset-[3px] rounded-full bg-card" />
            <ShieldCheck className="relative size-6 text-primary" />
          </div>
          <h1 className="text-xl font-black">کد تأیید</h1>
          {/* channel-aware: the server's own message names SMS vs email */}
          <p className="mt-1.5 text-sm leading-6 text-muted-foreground">{step.hint}</p>
        </div>

        {formError && (
          <div role="alert" className="mb-4 flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" />
            <span>{formError}</span>
          </div>
        )}
        {infoMessage && !formError && (
          <div role="status" className="mb-4 rounded-xl border border-success/30 bg-success/10 p-3 text-sm text-success">
            {infoMessage}
          </div>
        )}
        {step.debugCode && (
          <div role="status" className="mb-4 rounded-xl border border-info/30 bg-info/10 p-3 text-center text-sm text-info">
            کد تست: <span dir="ltr" className="font-bold tabular">{step.debugCode}</span>
          </div>
        )}

        <form onSubmit={submitVerify} className="flex flex-col gap-4">
          <Input
            dir="ltr"
            inputMode="numeric"
            autoComplete="one-time-code"
            autoFocus
            placeholder="کد"
            value={code}
            onChange={(e) => setCode(parseDigits(e.target.value).replace(/\D/g, "").slice(0, 8))}
            className="h-12 text-center text-xl tracking-[0.4em]"
            aria-label="کد تأیید"
          />
          <Button type="submit" className="h-11" disabled={busy || code.length < 4}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            تأیید و ورود
          </Button>
        </form>

        <div className="mt-4 flex flex-col items-center gap-2">
          {cooldownLeft > 0 ? (
            <p className="text-xs text-muted-foreground">ارسال دوباره تا {cooldownLeft} ثانیهٔ دیگر</p>
          ) : (
            <Button variant="ghost" size="sm" onClick={resend} disabled={busy}>
              ارسال دوباره کد
            </Button>
          )}
          <button onClick={backToAuth} className="text-sm text-muted-foreground hover:text-foreground">
            بازگشت به ورود
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full max-w-[400px]">
      <div className="mb-7 flex flex-col items-center text-center">
        <div className="relative mb-4 grid size-[62px] place-items-center rounded-full bg-linear-to-br from-indigo-500 to-violet-600 shadow-[0_0_40px_-6px_rgb(99_102_241/0.8)]">
          <div className="absolute inset-[3px] rounded-full bg-card" />
          {step.tab === "login" ? <KeyRound className="relative size-6 text-primary" /> : <UserRound className="relative size-6 text-primary" />}
        </div>
        <h1 className="text-xl font-black">پورتال مشتریان</h1>
        <p className="mt-1.5 text-sm leading-6 text-muted-foreground">
          {step.tab === "login" ? "برای پیگیری درخواست‌های ملکی خود وارد شوید." : "برای ثبت درخواست ملک، یک حساب بسازید."}
        </p>
      </div>

      <Tabs
        value={step.tab}
        onValueChange={(v) => {
          setFormError(null);
          setStep({ kind: "auth", tab: v as "login" | "register" });
        }}
        className="w-full items-center"
      >
        <TabsList className="mb-5 w-full">
          <TabsTrigger value="login">ورود</TabsTrigger>
          <TabsTrigger value="register">ثبت‌نام</TabsTrigger>
        </TabsList>

        {formError && (
          <div role="alert" className="mb-4 flex w-full items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" />
            <span>{formError}</span>
          </div>
        )}

        <TabsContent value="login" className="w-full">
        <form onSubmit={submitLogin} className="flex flex-col gap-4">
          <AuthField id="li-id" label="شماره موبایل یا ایمیل">
            <Input
              id="li-id"
              dir="ltr"
              autoComplete="username"
              inputMode="email"
              autoCapitalize="none"
              spellCheck={false}
              maxLength={200}
              placeholder="۰۹۱۲۳۴۵۶۷۸۹ یا you@example.com"
              required
              value={liId}
              onChange={(e) => setLiId(e.target.value)}
              className="h-11 text-left"
            />
          </AuthField>
          <AuthField id="li-pass" label="رمز عبور">
            <PasswordInput id="li-pass" value={liPw} onChange={setLiPw} autoComplete="current-password" onCapsLock={setLiCaps} />
            {liCaps && <p className="text-xs text-warning">Caps Lock روشن است.</p>}
          </AuthField>
          <label className="flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
            <Checkbox checked={remember} onCheckedChange={(v) => setRemember(v === true)} />
            مرا به خاطر بسپار
          </label>
          <Button type="submit" disabled={busy} className="h-11 text-[15px] shadow-[0_8px_24px_-8px_rgb(99_102_241/0.8)]">
            {busy && <Loader2 className="size-4 animate-spin" />}
            ورود
          </Button>
        </form>
        </TabsContent>

        <TabsContent value="register" className="w-full">
        <form onSubmit={submitRegister} className="flex flex-col gap-4">
          <AuthField id="rg-name" label="نام و نام خانوادگی" error={rgErrors.name}>
            <Input
              id="rg-name"
              autoComplete="name"
              maxLength={200}
              placeholder="مثلاً علی محمدی"
              value={rgName}
              onChange={(e) => setRgName(e.target.value)}
              onBlur={() => rgName.trim() && setRgErrors((s) => ({ ...s, name: rgName.trim().length >= 2 ? "" : "نام را کامل وارد کنید" }))}
              className="h-11"
            />
          </AuthField>
          <AuthField id="rg-phone" label="شماره موبایل" error={rgErrors.phone}>
            <Input
              id="rg-phone"
              dir="ltr"
              inputMode="tel"
              maxLength={11}
              placeholder="09123456789"
              autoComplete="tel"
              value={rgPhone}
              onChange={(e) => setRgPhone(parseDigits(e.target.value).replace(/\D/g, "").slice(0, 11))}
              onBlur={() => rgPhone && setRgErrors((s) => ({ ...s, phone: PHONE_RE.test(rgPhone) ? "" : "شماره باید با فرمت ۰۹۱۲۳۴۵۶۷۸۹ باشد" }))}
              className="h-11 text-left"
            />
          </AuthField>
          <AuthField id="rg-email" label="ایمیل" error={rgErrors.email}>
            <Input
              id="rg-email"
              type="email"
              dir="ltr"
              autoComplete="email"
              maxLength={200}
              placeholder="you@example.com"
              autoCapitalize="none"
              spellCheck={false}
              value={rgEmail}
              onChange={(e) => setRgEmail(e.target.value)}
              onBlur={() => rgEmail.trim() && setRgErrors((s) => ({ ...s, email: EMAIL_RE.test(rgEmail.trim()) ? "" : "ایمیل معتبر وارد کنید" }))}
              className="h-11 text-left"
            />
            <p className="text-xs text-muted-foreground">اگر شماره در دسترس نباشد، کد ورود به همین آدرس فرستاده می‌شود.</p>
          </AuthField>
          <div className="grid gap-1.5 text-start">
            <Label htmlFor="rg-pass">رمز عبور</Label>
            <PasswordInput id="rg-pass" value={rgPw} onChange={setRgPw} autoComplete="new-password" onCapsLock={setRgCaps} />
            {rgCaps && <p className="text-xs text-warning">Caps Lock روشن است.</p>}
            {(() => {
              const score = pwScore(rgPw);
              const met = Object.values(score).filter(Boolean).length;
              const s = STRENGTH[rgPw ? met : 0];
              return (
                <>
                  <Progress value={s.w} className={s.cls} aria-label="قدرت رمز عبور" />
                  <p className="text-xs text-muted-foreground">{s.label}</p>
                  <ul className="mt-0.5 grid grid-cols-2 gap-x-3 gap-y-0.5">
                    {RULES.map((r) => (
                      <li key={r.key} className={`flex items-center gap-1.5 text-[11px] ${score[r.key] ? "text-success" : "text-muted-foreground"}`}>
                        <span aria-hidden>{score[r.key] ? "●" : "○"}</span>
                        {r.label}
                      </li>
                    ))}
                  </ul>
                </>
              );
            })()}
            {rgErrors.password && <p role="alert" className="text-xs text-destructive">{rgErrors.password}</p>}
          </div>
          <label className="flex cursor-pointer items-start gap-2 text-sm text-muted-foreground">
            <Checkbox checked={rgOptIn} onCheckedChange={(v) => setRgOptIn(v === true)} className="mt-0.5" />
            <span>مایلم آگهی‌ها و پیشنهادهای ملکی برایم ایمیل شود</span>
          </label>
          <Button type="submit" disabled={busy} className="h-11 text-[15px] shadow-[0_8px_24px_-8px_rgb(99_102_241/0.8)]">
            {busy && <Loader2 className="size-4 animate-spin" />}
            ثبت‌نام و دریافت کد
          </Button>
        </form>
        </TabsContent>
      </Tabs>

      <p className="mt-5 flex items-center justify-center gap-1.5 text-center text-xs text-muted-foreground">
        <Mail className="size-3.5" />
        کاربر پنل هستید؟ <Link href="/panel/login" className="text-primary hover:underline">ورود به پنل مدیریت</Link>
      </p>
    </div>
  );
}
