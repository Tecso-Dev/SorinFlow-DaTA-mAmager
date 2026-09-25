"use client";

// امنیت — change password (bumps token_version: every other device is
// signed out; this device gets a fresh cookie, so the session is refetched),
// TOTP status + the setup/disable dialog, and the email second-factor toggle.

import { KeyRound, Loader2, Mail } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Field, Section, ToneBadge } from "@/components/panel/kit";
import { Reveal } from "@/components/viz";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { SESSION_KEY, type User } from "@/lib/session";
import { TotpDialog } from "./totp-dialog";

function PasswordForm() {
  const qc = useQueryClient();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (next.length < 8) {
      toast.error("رمز تازه باید دست‌کم ۸ نویسه باشد");
      return;
    }
    if (next !== confirm) {
      toast.error("تکرار رمز یکی نیست");
      return;
    }
    setBusy(true);
    try {
      const r = await api<{ message: string }>("/users/me/password", {
        method: "POST", json: { current_password: current, new_password: next },
      });
      setCurrent(""); setNext(""); setConfirm("");
      // The backend re-issued the session cookie for this device; the old
      // cached user (and CSRF token) must not linger.
      await qc.invalidateQueries({ queryKey: SESSION_KEY });
      toast.success(r.message || "رمز عوض شد", "دستگاه‌های دیگر از حساب خارج شدند");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "تغییر رمز ناموفق بود");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
      <Field label="رمز فعلی" htmlFor="pf-pw-current" className="sm:col-span-2">
        <Input id="pf-pw-current" type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" />
      </Field>
      <Field label="رمز تازه" htmlFor="pf-pw-new" hint="دست‌کم ۸ نویسه">
        <Input id="pf-pw-new" type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" />
      </Field>
      <Field label="تکرار رمز تازه" htmlFor="pf-pw-new2">
        <Input id="pf-pw-new2" type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" />
      </Field>
      <div className="sm:col-span-2">
        <Button type="submit" disabled={busy} className="gap-1.5">
          {busy && <Loader2 className="size-4 animate-spin" />}
          تغییر رمز عبور
        </Button>
      </div>
    </form>
  );
}

export function SecurityCard({ me }: { me: User }) {
  const qc = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [email2faBusy, setEmail2faBusy] = useState(false);

  const totp = useQuery({
    queryKey: ["profile", "totp-status"],
    queryFn: () => api<{ enabled: boolean }>("/users/me/totp/status"),
  });

  async function refresh() {
    await Promise.all([
      qc.invalidateQueries({ queryKey: SESSION_KEY }),
      qc.invalidateQueries({ queryKey: ["profile", "totp-status"] }),
    ]);
  }

  async function toggleEmail2fa(checked: boolean) {
    setEmail2faBusy(true);
    try {
      const r = await api<{ message: string }>("/users/me/email-2fa", { method: "POST", json: { enabled: checked } });
      await qc.invalidateQueries({ queryKey: SESSION_KEY });
      toast.success(r.message);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "ذخیره نشد");
    } finally {
      setEmail2faBusy(false);
    }
  }

  return (
    <Reveal delay={0.2}>
      <Section title="امنیت">
        <div className="flex flex-col gap-5">
          <PasswordForm />

          <div className="border-t pt-4">
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border p-3">
              <div className="flex items-center gap-2">
                <KeyRound className="size-4 text-muted-foreground" />
                <div>
                  <div className="text-sm font-semibold">احراز هویت دو مرحله‌ای (برنامه)</div>
                  <div className="text-xs text-muted-foreground">Google Authenticator یا مشابه</div>
                </div>
              </div>
              <div className="flex items-center gap-2">
                {totp.isPending ? null : (
                  <ToneBadge tone={totp.data?.enabled ? "success" : "neutral"}>
                    {totp.data?.enabled ? "فعال" : "غیرفعال"}
                  </ToneBadge>
                )}
                <Button size="sm" variant="outline" onClick={() => setDialogOpen(true)} disabled={totp.isPending}>
                  {totp.data?.enabled ? "غیرفعال‌سازی" : "راه‌اندازی"}
                </Button>
              </div>
            </div>

            <label className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border p-3">
              <span className="flex items-center gap-2">
                <Mail className="size-4 text-muted-foreground" />
                <span>
                  <span className="block text-sm font-semibold">ورود دومرحله‌ای با ایمیل</span>
                  <span className="block text-xs text-muted-foreground">
                    وقتی برنامهٔ احراز هویت فعال نیست، کد به ایمیل فرستاده می‌شود
                  </span>
                </span>
              </span>
              <Switch
                checked={me.email_2fa_enabled}
                onCheckedChange={toggleEmail2fa}
                disabled={email2faBusy || !me.email}
                aria-label="ورود دومرحله‌ای با ایمیل"
              />
            </label>
            {!me.email && <p className="mt-1.5 text-xs text-muted-foreground">برای این کار باید ابتدا ایمیل خود را ثبت و تأیید کنید.</p>}
          </div>
        </div>
      </Section>

      {totp.data && (
        <TotpDialog open={dialogOpen} onOpenChange={setDialogOpen} enabled={totp.data.enabled} onChanged={refresh} />
      )}
    </Reveal>
  );
}
