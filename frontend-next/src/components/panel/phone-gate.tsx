"use client";

// «شماره‌ات را تأیید کن»: a route that needs the caller's own verified
// number answers 403 phone_unverified; this dialog sends the SMS code,
// checks it, and lets api() make the refused call again.

import { Smartphone } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError, setPhoneGate, type PhoneGateDetail } from "@/lib/api";
import { parseDigits } from "@/lib/format";
import { SESSION_KEY } from "@/lib/session";
import { Field, RingDialog } from "./kit";

export function PhoneGate() {
  const qc = useQueryClient();
  const [detail, setDetail] = useState<PhoneGateDetail | null>(null);
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const resolver = useRef<((ok: boolean) => void) | null>(null);

  useEffect(() => {
    setPhoneGate(
      (d) =>
        new Promise<boolean>((res) => {
          resolver.current?.(false);
          resolver.current = res;
          setDetail(d);
          setPhone(d.phone ?? "");
          setCode("");
          setSent(false);
          setError("");
        }),
    );
    return () => setPhoneGate(null);
  }, []);

  function close(ok: boolean) {
    resolver.current?.(ok);
    resolver.current = null;
    setDetail(null);
  }

  async function send() {
    setBusy(true);
    setError("");
    try {
      const r = await api<{ sent: boolean; verified: boolean }>("/users/me/phone/request", {
        json: { phone: parseDigits(phone).trim() || null },
      });
      if (r.verified) {
        await qc.invalidateQueries({ queryKey: SESSION_KEY });
        return close(true);
      }
      setSent(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "کد فرستاده نشد");
    } finally {
      setBusy(false);
    }
  }

  async function verify(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/users/me/phone/verify", { json: { code: parseDigits(code).trim() } });
      await qc.invalidateQueries({ queryKey: SESSION_KEY });
      close(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "کد درست نیست");
    } finally {
      setBusy(false);
    }
  }

  return (
    <RingDialog
      open={!!detail}
      onOpenChange={(o) => !o && close(false)}
      icon={Smartphone}
      title="تأیید شمارهٔ موبایل"
      description={detail?.message}
    >
      {!sent ? (
        <div className="grid gap-3">
          <Field label="شمارهٔ موبایل شما" htmlFor="gate-phone" error={error || undefined}>
            <Input id="gate-phone" dir="ltr" inputMode="tel" placeholder="09123456789" value={phone} onChange={(e) => setPhone(e.target.value)} />
          </Field>
          <Button className="w-full" disabled={busy} onClick={send}>ارسال کد تأیید</Button>
          <Button variant="ghost" className="w-full" onClick={() => close(false)}>بعداً</Button>
        </div>
      ) : (
        <form onSubmit={verify} className="grid gap-3">
          <Field label="کد پیامک‌شده" htmlFor="gate-code" hint={`به ${phone}`} error={error || undefined}>
            <Input id="gate-code" dir="ltr" inputMode="numeric" autoComplete="one-time-code" autoFocus value={code} onChange={(e) => setCode(e.target.value)} />
          </Field>
          <Button type="submit" className="w-full" disabled={busy || code.trim().length < 4}>تأیید و ادامه</Button>
          <Button type="button" variant="ghost" className="w-full" disabled={busy} onClick={send}>ارسال دوبارهٔ کد</Button>
        </form>
      )}
    </RingDialog>
  );
}
