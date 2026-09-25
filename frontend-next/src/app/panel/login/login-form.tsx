"use client";

import { ArrowRight, Eye, EyeOff, KeyRound, Loader2, Mail, ShieldCheck, TriangleAlert } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { InputOTP, InputOTPGroup, InputOTPSlot } from "@/components/ui/input-otp";
import { Label } from "@/components/ui/label";
import { useNonce } from "@/components/nonce";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { parseDigits } from "@/lib/format";
import { SESSION_KEY, type Session } from "@/lib/session";

type LoginReply =
  | (Session & { ok: true })
  | { ok?: false; requires_totp: true; totp_session: string }
  | { ok?: false; requires_email_code: true; email_session: string; email_hint?: string };

type Step =
  | { kind: "password" }
  | { kind: "totp"; session: string }
  | { kind: "email"; session: string; hint?: string }
  | { kind: "forgot" }
  | { kind: "reset"; identifier: string };

/** Only a path inside the panel is a safe place to go back to. */
function safeNext(next: string | null) {
  return next && next.startsWith("/panel") && !next.startsWith("//") ? next : "/panel";
}

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const nonce = useNonce();

  const [step, setStep] = useState<Step>({ kind: "password" });
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [caps, setCaps] = useState(false);
  const [remember, setRemember] = useState(true);
  const [code, setCode] = useState("");
  const [newPw, setNewPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function finish(reply: Session) {
    qc.setQueryData(SESSION_KEY, { user: reply.user, csrf_token: reply.csrf_token });
    router.replace(safeNext(params.get("next")));
  }

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "خطای ناشناخته؛ دوباره امتحان کنید.");
    } finally {
      setBusy(false);
    }
  }

  const submitPassword = (e: React.FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const r = await api<LoginReply>("/session/login", { json: { username: username.trim(), password, remember } });
      if ("requires_totp" in r && r.requires_totp) return setStep({ kind: "totp", session: r.totp_session });
      if ("requires_email_code" in r && r.requires_email_code)
        return setStep({ kind: "email", session: r.email_session, hint: r.email_hint });
      finish(r as Session);
    });
  };

  const submitCode = (value = code) => {
    if (step.kind !== "totp" && step.kind !== "email") return;
    const clean = parseDigits(value).replace(/\D/g, "");
    if (clean.length < 4) return;
    void run(async () => {
      const r =
        step.kind === "totp"
          ? await api<Session>("/session/verify-totp", { json: { totp_session: step.session, code: clean, remember } })
          : await api<Session>("/session/verify-email", { json: { email_session: step.session, code: clean, remember } });
      finish(r);
    });
  };

  const submitForgot = (e: React.FormEvent) => {
    e.preventDefault();
    const identifier = username.trim();
    void run(async () => {
      await api("/users/password-reset/request", { json: { identifier } });
      setCode("");
      setStep({ kind: "reset", identifier });
    });
  };

  const submitReset = (e: React.FormEvent) => {
    e.preventDefault();
    if (step.kind !== "reset") return;
    void run(async () => {
      await api("/users/password-reset/confirm", {
        json: { identifier: step.identifier, code: parseDigits(code).replace(/\D/g, ""), new_password: newPw },
      });
      toast.success("رمز تازه ثبت شد", "حالا با رمز تازه وارد شوید.");
      setPassword("");
      setStep({ kind: "password" });
    });
  };

  const back = () => {
    setError(null);
    setCode("");
    setStep({ kind: "password" });
  };

  const heading = {
    password: { icon: KeyRound, title: "ورود به پنل", sub: "نام کاربری یا ایمیل و رمز خود را وارد کنید." },
    totp: { icon: ShieldCheck, title: "کد ورود دومرحله‌ای", sub: "کد ۶ رقمی برنامهٔ احراز هویت (Google Authenticator و مانند آن) را وارد کنید." },
    email: {
      icon: Mail,
      title: "کد ارسال‌شده به ایمیل",
      sub: step.kind === "email" && step.hint ? `کد به ${step.hint} فرستاده شد.` : "کدی را که به ایمیلتان فرستاده شد وارد کنید.",
    },
    forgot: { icon: KeyRound, title: "بازیابی رمز", sub: "نام کاربری یا ایمیل حساب را بنویسید تا کد بازیابی برایتان فرستاده شود." },
    reset: { icon: KeyRound, title: "رمز تازه", sub: "اگر حسابی با این مشخصات باشد، کد برایش فرستاده شد. کد و رمز تازه را وارد کنید." },
  }[step.kind];

  return (
    <div className="w-full max-w-[400px]">
      <div className="mb-7 flex flex-col items-center text-center">
        {/* the OTP dialog's ring, kept from the current panel */}
        <div className="relative mb-4 grid size-[62px] place-items-center rounded-full bg-linear-to-br from-indigo-500 to-violet-600 shadow-[0_0_40px_-6px_rgb(99_102_241/0.8)]">
          <div className="absolute inset-[3px] rounded-full bg-card" />
          <heading.icon className="relative size-6 text-primary" />
        </div>
        <h1 className="text-xl font-black">{heading.title}</h1>
        <p className="mt-1.5 text-sm leading-6 text-muted-foreground">{heading.sub}</p>
      </div>

      {error && (
        <div role="alert" className="mb-4 flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {step.kind === "password" && (
        <form onSubmit={submitPassword} className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="username">نام کاربری یا ایمیل</Label>
            <Input
              id="username"
              dir="ltr"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              required
              minLength={2}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="h-11 text-left"
            />
          </div>
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="password">رمز عبور</Label>
              <button
                type="button"
                className="text-xs text-muted-foreground hover:text-primary"
                onClick={() => {
                  setError(null);
                  setStep({ kind: "forgot" });
                }}
              >
                رمز را فراموش کرده‌ام
              </button>
            </div>
            <div className="relative">
              <Input
                id="password"
                dir="ltr"
                type={showPw ? "text" : "password"}
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyUp={(e) => setCaps(e.getModifierState("CapsLock"))}
                className="h-11 pe-11 text-left"
              />
              <button
                type="button"
                onClick={() => setShowPw((v) => !v)}
                className="absolute inset-y-0 end-0 grid w-11 place-items-center text-muted-foreground hover:text-foreground"
                aria-label={showPw ? "پنهان کردن رمز" : "نمایش رمز"}
              >
                {showPw ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
              </button>
            </div>
            {caps && <p className="text-xs text-warning">Caps Lock روشن است.</p>}
          </div>
          <label className="flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
            <Checkbox checked={remember} onCheckedChange={(v) => setRemember(v === true)} />
            مرا روی این دستگاه به خاطر بسپار
          </label>
          <Button type="submit" disabled={busy} className="h-11 text-[15px] shadow-[0_8px_24px_-8px_rgb(99_102_241/0.8)]">
            {busy && <Loader2 className="size-4 animate-spin" />}
            ورود
          </Button>
        </form>
      )}

      {step.kind === "totp" && (
        <div className="flex flex-col items-center gap-5">
          <div dir="ltr">
            <InputOTP
              maxLength={6}
              value={code}
              onChange={setCode}
              onComplete={submitCode}
              autoFocus
              nonce={nonce}
              pasteTransformer={(t) => parseDigits(t).replace(/\D/g, "")}
              aria-label="کد ۶ رقمی"
            >
              <InputOTPGroup>
                {Array.from({ length: 6 }, (_, i) => (
                  <InputOTPSlot key={i} index={i} className="size-12 text-lg" />
                ))}
              </InputOTPGroup>
            </InputOTP>
          </div>
          <Button className="h-11 w-full" disabled={busy || code.length < 6} onClick={() => submitCode()}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            تأیید و ورود
          </Button>
        </div>
      )}

      {step.kind === "email" && (
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            submitCode();
          }}
        >
          <Input
            dir="ltr"
            inputMode="numeric"
            autoComplete="one-time-code"
            autoFocus
            placeholder="کد"
            value={code}
            onChange={(e) => setCode(parseDigits(e.target.value).replace(/\D/g, "").slice(0, 8))}
            className="h-12 text-center text-xl tracking-[0.4em]"
            aria-label="کد ایمیل"
          />
          <Button type="submit" className="h-11" disabled={busy || code.length < 4}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            تأیید و ورود
          </Button>
        </form>
      )}

      {step.kind === "forgot" && (
        <form onSubmit={submitForgot} className="flex flex-col gap-4">
          <Input
            dir="ltr"
            autoComplete="username"
            required
            minLength={3}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="h-11 text-left"
            aria-label="نام کاربری یا ایمیل"
          />
          <Button type="submit" className="h-11" disabled={busy}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            فرستادن کد بازیابی
          </Button>
        </form>
      )}

      {step.kind === "reset" && (
        <form onSubmit={submitReset} className="flex flex-col gap-4">
          <Input
            dir="ltr"
            inputMode="numeric"
            autoComplete="one-time-code"
            placeholder="کد بازیابی"
            required
            value={code}
            onChange={(e) => setCode(parseDigits(e.target.value).replace(/\D/g, "").slice(0, 8))}
            className="h-11 text-center tracking-[0.3em]"
            aria-label="کد بازیابی"
          />
          <Input
            dir="ltr"
            type="password"
            autoComplete="new-password"
            placeholder="رمز تازه (دست‌کم ۸ نویسه)"
            required
            minLength={8}
            maxLength={128}
            value={newPw}
            onChange={(e) => setNewPw(e.target.value)}
            className="h-11 text-left placeholder:text-right"
            aria-label="رمز تازه"
          />
          <Button type="submit" className="h-11" disabled={busy}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            ثبت رمز تازه
          </Button>
        </form>
      )}

      {step.kind !== "password" && (
        <button onClick={back} className="mx-auto mt-5 flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
          <ArrowRight className="size-4" />
          بازگشت به ورود
        </button>
      )}
    </div>
  );
}
