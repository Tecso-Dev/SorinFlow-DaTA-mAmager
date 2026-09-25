"use client";

// «ورود به حساب دیوار»: phone in, Divar SMS code in, done. Two steps in one
// RingDialog — the OTP window's own look (CLAUDE.md: هر دیالوگ کد باید از
// همین ظاهر استفاده کند) — opened from a button on the page, not inline, so
// the page stays short when nobody is logging a number in right now.

import { KeyRound, Send } from "lucide-react";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Field, RingDialog, Section } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import { parseDigits } from "@/lib/format";
import type { AuthResponse } from "./types";
import { SixDigitOtp } from "./otp-input";

const PHONE_RE = /^09\d{9}$/;

export function LoginCard() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [phone, setPhone] = useState("");
  const [step, setStep] = useState<"phone" | "otp">("phone");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function reset() {
    setOpen(false);
    setStep("phone");
    setPhone("");
    setCode("");
    setError("");
  }

  async function sendCode() {
    const p = parseDigits(phone).trim();
    if (!PHONE_RE.test(p)) {
      setError("شمارهٔ موبایل معتبر وارد کنید (مثل ۰۹۱۲۳۴۵۶۷۸۹)");
      return;
    }
    setPhone(p);
    setBusy(true);
    setError("");
    try {
      const r = await api<AuthResponse>("/auth/login", { json: { phone_number: p } });
      if (r.requires_code) {
        setCode("");
        setStep("otp");
      } else {
        setError(r.message || "کد فرستاده نشد");
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "کد فرستاده نشد");
    } finally {
      setBusy(false);
    }
  }

  async function verify(value: string) {
    if (value.length !== 6 || busy) return;
    setBusy(true);
    setError("");
    try {
      const r = await api<AuthResponse>(`/auth/verify?phone_number=${encodeURIComponent(phone)}`, { json: { code: value } });
      if (r.success) {
        toast.success("ورود موفق بود", phone);
        qc.invalidateQueries({ queryKey: ["divar", "cookies"] });
        qc.invalidateQueries({ queryKey: ["divar", "registry"] });
        reset();
      } else {
        setError(r.message || "کد درست نیست");
        setCode("");
      }
    } catch (e) {
      // 403 «شمارهٔ کس دیگری», 409 «کاربر دیگری در حال ورود است» یا «ری‌استارت شد، دوباره کد بگیرید»
      setError(e instanceof ApiError ? e.message : "کد تأیید نشد");
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section
      title="ورود به حساب دیوار"
      hint="یک شمارهٔ دیوار تازه را با کد پیامکی به این پنل وصل کنید"
    >
      <Button onClick={() => setOpen(true)} className="shadow-[0_8px_24px_-8px_rgb(99_102_241/0.8)]">
        <KeyRound /> ورود با شمارهٔ تازه
      </Button>

      <RingDialog
        open={open}
        onOpenChange={(o) => (o ? setOpen(true) : reset())}
        icon={step === "phone" ? Send : KeyRound}
        title={step === "phone" ? "شمارهٔ دیوار" : "کد تأیید"}
        description={step === "phone" ? "کد تأیید دیوار به این شماره پیامک می‌شود" : `کد پیامک‌شده به ${phone} را وارد کنید`}
      >
        {step === "phone" ? (
          <form
            className="grid gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              void sendCode();
            }}
          >
            <Field label="شمارهٔ موبایل دیوار" htmlFor="divar-login-phone" error={error || undefined}>
              <Input
                id="divar-login-phone"
                dir="ltr"
                inputMode="tel"
                placeholder="09123456789"
                autoFocus
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
              />
            </Field>
            <Button type="submit" className="w-full" disabled={busy}>ارسال کد تأیید</Button>
            <Button type="button" variant="ghost" className="w-full" onClick={reset}>انصراف</Button>
          </form>
        ) : (
          <div className="grid gap-3">
            <SixDigitOtp value={code} onChange={setCode} onComplete={verify} disabled={busy} autoFocus aria-label="کد تأیید دیوار" />
            {error && <p className="text-center text-xs text-destructive">{error}</p>}
            <Button className="w-full" disabled={busy || code.length !== 6} onClick={() => verify(code)}>تأیید و ورود</Button>
            <Button variant="ghost" className="w-full" disabled={busy} onClick={() => setStep("phone")}>بازگشت — ارسال مجدد</Button>
          </div>
        )}
      </RingDialog>
    </Section>
  );
}
