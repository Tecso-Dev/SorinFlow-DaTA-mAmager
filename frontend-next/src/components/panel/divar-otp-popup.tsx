"use client";

// The global Divar OTP popup — the heart of the scraper's login experience,
// generalized from the old panel's per-section modal (inventory ۱.۴) into
// one dialog mounted once in the shell, for anybody with `scraper` or
// `divar_auth`. Polls /scraper/otp-pending every 4s while the panel is
// mounted; the endpoint itself narrows pending prompts to this person's own
// numbers/jobs (or all of them for root/super_admin) — this component never
// re-filters on top of that, and never shows a raw phone: only the masked
// `phone_hint` the server sends.

import { IdCard, PhoneOff, RefreshCw, ShieldQuestion, SkipForward, Smartphone } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RingDialog } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import { faNum } from "@/lib/format";
import { can, useSession } from "@/lib/session";
import { SixDigitOtp } from "@/components/divar/otp-input";
import type { CookiesResponse, IdentityRequired, OtpPendingResponse, PendingOtp } from "@/components/divar/types";

const onlyDigits = (s: string) => (s || "").replace(/\D/g, "");
const jobOf = (key: string) => key.split(":")[0];

/** A ring that drains as the server's countdown does — never a client-made
 *  number, only how it is drawn. */
function CountdownRing({ remaining, total }: { remaining: number; total: number }) {
  const r = 26;
  const c = 2 * Math.PI * r;
  const frac = total > 0 ? Math.max(0, Math.min(1, remaining / total)) : 0;
  return (
    <div className="relative grid size-16 place-items-center">
      <svg width="64" height="64" viewBox="0 0 64 64" className="-rotate-90" aria-hidden>
        <circle cx="32" cy="32" r={r} className="stroke-muted" strokeWidth="4" fill="none" />
        <motion.circle
          cx="32" cy="32" r={r} className="stroke-primary" strokeWidth="4" fill="none" strokeLinecap="round"
          strokeDasharray={c}
          animate={{ strokeDashoffset: c * (1 - frac) }}
          transition={{ duration: 0.6, ease: "linear" }}
        />
      </svg>
      <span className="absolute text-sm font-bold tabular">{faNum(remaining)}</span>
    </div>
  );
}

async function resolveCookieId(phone: string): Promise<number | null> {
  const digits = onlyDigits(phone);
  const mine = await api<CookiesResponse>("/auth/cookies?mine=1").catch(() => null);
  const own = mine?.cookies.find((c) => onlyDigits(c.phone_number) === digits);
  if (own) return own.id;
  // Unclaimed number: only root/super_admin ever see it in identity_required
  // in the first place (the endpoint's own filtering), and only they can see
  // it here too.
  const all = await api<CookiesResponse>("/auth/cookies").catch(() => null);
  return all?.cookies.find((c) => onlyDigits(c.phone_number) === digits)?.id ?? null;
}

export function DivarOtpPopup() {
  const user = useSession().data?.user;
  const qc = useQueryClient();
  const allowed = !!user && (can(user, { perm: "scraper" }) || can(user, { perm: "divar_auth" }));

  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const [resendLockUntil, setResendLockUntil] = useState(0);
  const [resendRemaining, setResendRemaining] = useState(0);
  const shownKey = useRef<string | null>(null);

  // Polling lives in this component's own effect (via the query's interval,
  // which starts and stops with this component's mount — not a module-level
  // timer), exactly as the old panel's startOtpPolling/stopOtpPolling did
  // for the section it belonged to.
  const poll = useQuery({
    queryKey: ["divar", "otp-pending"],
    queryFn: () => api<OtpPendingResponse>("/scraper/otp-pending"),
    enabled: allowed,
    refetchInterval: 4000,
    refetchIntervalInBackground: true,
  });

  const identity: IdentityRequired[] = poll.data?.identity_required ?? [];
  const pending: PendingOtp[] = (poll.data?.pending ?? []).filter((p) => !dismissed.has(p.key));
  const current = pending[0] ?? null;
  const timeout = poll.data?.timeout ?? 120;

  // The countdown ticks locally between polls, but every fresh poll and
  // every new prompt re-syncs it to the server's own number.
  useEffect(() => {
    if (!current) return;
    if (shownKey.current !== current.key) {
      shownKey.current = current.key;
      setCode("");
      setSecondsLeft(current.remaining);
    } else {
      setSecondsLeft(current.remaining);
    }
  }, [current?.key, current?.remaining]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!current) return;
    const t = setInterval(() => setSecondsLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(t);
  }, [current?.key]); // eslint-disable-line react-hooks/exhaustive-deps

  // The 15s client-side resend lockout, ticked into state from the interval
  // callback only (never read from Date.now() during render).
  useEffect(() => {
    if (!resendLockUntil) return;
    const tick = () => setResendRemaining(Math.max(0, Math.ceil((resendLockUntil - Date.now()) / 1000)));
    const t = setInterval(tick, 500);
    const first = setTimeout(tick, 0);
    return () => {
      clearInterval(t);
      clearTimeout(first);
    };
  }, [resendLockUntil]);

  if (!allowed) return null;

  async function submitCode(value: string) {
    if (!current || value.length !== 6 || busy) return;
    setBusy(true);
    try {
      await api(`/scraper/otp/${current.key}`, { json: { code: value } });
      toast.success("کد تأیید شد");
      setDismissed((d) => new Set(d).add(current.key));
      qc.invalidateQueries({ queryKey: ["divar", "otp-pending"] });
    } catch (e) {
      toast.error("کد پذیرفته نشد", e instanceof ApiError ? e.message : undefined);
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    if (!current || resendRemaining > 0) return;
    setResendLockUntil(Date.now() + 15_000);
    try {
      const r = await api<{ ok: boolean; message: string }>(`/scraper/otp/${current.key}/resend`, { method: "POST" });
      toast.info("درخواست ارسال دوباره", r.message);
    } catch (e) {
      toast.error("ارسال دوباره ممکن نشد", e instanceof ApiError ? e.message : undefined);
    }
  }

  async function switchNumber() {
    if (!current) return;
    setBusy(true);
    try {
      const r = await api<{ success: boolean; message: string }>(`/scraper/jobs/${jobOf(current.key)}/switch-account`, {
        json: {},
      });
      toast.success("تعویض شماره ثبت شد", r.message);
      setDismissed((d) => new Set(d).add(current.key));
      qc.invalidateQueries({ queryKey: ["divar", "otp-pending"] });
    } catch (e) {
      toast.error("تعویض شماره ممکن نشد", e instanceof ApiError ? e.message : undefined);
    } finally {
      setBusy(false);
    }
  }

  async function dismiss() {
    if (!current) return;
    setBusy(true);
    try {
      await api(`/scraper/otp-cancel?job_id=${encodeURIComponent(jobOf(current.key))}`, { method: "POST" });
      setDismissed((d) => new Set(d).add(current.key));
      qc.invalidateQueries({ queryKey: ["divar", "otp-pending"] });
    } catch (e) {
      toast.error("انجام نشد", e instanceof ApiError ? e.message : undefined);
    } finally {
      setBusy(false);
    }
  }

  async function identityCleared(item: IdentityRequired) {
    setBusy(true);
    try {
      const id = await resolveCookieId(item.phone);
      if (id == null) throw new Error("این شماره پیدا نشد");
      await api(`/auth/cookies/${id}/identity-cleared`, { method: "POST" });
      toast.success("ثبت شد", "این شماره دوباره در چرخش قرار می‌گیرد");
      qc.invalidateQueries({ queryKey: ["divar", "otp-pending"] });
      qc.invalidateQueries({ queryKey: ["divar", "cookies"] });
    } catch (e) {
      toast.error("ثبت نشد", e instanceof ApiError ? e.message : e instanceof Error ? e.message : undefined);
    } finally {
      setBusy(false);
    }
  }

  const forwarderEntry = current
    ? Object.entries(poll.data?.forwarders ?? {}).find(([acct]) => onlyDigits(acct) === onlyDigits(current.phone_hint))
    : undefined;
  const mode = !forwarderEntry ? "دستی" : forwarderEntry[1].online ? "خودکار — گوشی متصل است" : "گوشی آفلاین";

  // Identity walls come first: nothing about a code answers them.
  if (identity.length > 0) {
    const item = identity[0];
    return (
      <RingDialog open icon={ShieldQuestion} onOpenChange={() => {}} title="دیوار احراز هویت می‌خواهد" tone="danger"
        description={`دیوار برای شمارهٔ ${item.phone} احراز هویت (کد ملی) می‌خواهد. اسکرپر نمی‌تواند این را حل کند — لطفاً در دیوار وارد شوید و احراز هویت را کامل کنید.`}
      >
        {item.text && <p className="rounded-lg bg-muted p-3 text-xs leading-6">{item.text}</p>}
        <Button className="mt-3 w-full" disabled={busy} onClick={() => identityCleared(item)}>
          <IdCard /> انجام شد — احراز هویت کردم
        </Button>
      </RingDialog>
    );
  }

  if (!current) return null;

  return (
    <RingDialog open icon={Smartphone} onOpenChange={() => {}} title="کد تأیید دیوار"
      description={`کد پیامک‌شده به ${current.phone_hint} را وارد کنید`}
    >
      <div className="flex flex-col items-center gap-3">
        <motion.div animate={{ scale: [1, 1.04, 1] }} transition={{ duration: 2, repeat: Infinity }}>
          <CountdownRing remaining={secondsLeft} total={timeout} />
        </motion.div>
        <SixDigitOtp value={code} onChange={setCode} onComplete={submitCode} disabled={busy} autoFocus aria-label="کد تأیید دیوار" />
        <span className="text-[11px] text-muted-foreground">{mode}</span>
      </div>
      <div className="mt-4 grid gap-2">
        <Button className="w-full" disabled={busy || code.length !== 6} onClick={() => submitCode(code)}>تأیید</Button>
        <Button variant="outline" className="w-full" disabled={busy || resendRemaining > 0} onClick={resend}>
          <RefreshCw /> ارسال دوبارهٔ کد{resendRemaining > 0 ? ` (${faNum(resendRemaining)})` : ""}
        </Button>
        <Button variant="ghost" className="w-full" disabled={busy} onClick={switchNumber}>
          <PhoneOff /> گوشی در دسترس نیست — تعویض شماره
        </Button>
        <Button variant="ghost" className="w-full text-muted-foreground" disabled={busy} onClick={dismiss}>
          <SkipForward /> رد کردن و ادامه بدون شماره
        </Button>
      </div>
    </RingDialog>
  );
}
