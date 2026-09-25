"use client";

// «دو مرحله‌ای (برنامه)» — TOTP setup (QR + manual secret + 6-digit confirm)
// and disable (re-enter password). Opened from the امنیت card.

import { Copy, KeyRound, Loader2, ShieldCheck } from "lucide-react";
import QRCode from "qrcode";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { InputOTP, InputOTPGroup, InputOTPSlot } from "@/components/ui/input-otp";
import { Field, RingDialog } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { useNonce } from "@/components/nonce";
import { api, ApiError } from "@/lib/api";

type SetupResponse = { secret: string; qr_uri: string; enabled: boolean };

function SetupPanel({ onEnabled }: { onEnabled: () => void }) {
  const nonce = useNonce();
  const [data, setData] = useState<SetupResponse | null>(null);
  const [qrSrc, setQrSrc] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api<SetupResponse>("/users/me/totp/setup", { method: "POST" })
      .then(async (d) => {
        if (cancelled) return;
        setData(d);
        const svg = await QRCode.toString(d.qr_uri, { type: "svg", margin: 1, width: 200 });
        if (!cancelled) setQrSrc(`data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`);
      })
      .catch((e) => toast.error(e instanceof ApiError ? e.message : "خطا در دریافت اطلاعات"));
    return () => { cancelled = true; };
  }, []);

  async function copySecret() {
    if (!data) return;
    try {
      await navigator.clipboard.writeText(data.secret);
      toast.success("کپی شد");
    } catch {
      toast.error("کپی نشد");
    }
  }

  async function enable() {
    setBusy(true);
    try {
      await api("/users/me/totp/enable", { method: "POST", json: { code } });
      toast.success("احراز هویت دو مرحله‌ای فعال شد");
      onEnabled();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "کد اشتباه است");
    } finally {
      setBusy(false);
    }
  }

  if (!data) return <div className="grid h-52 place-items-center"><Loader2 className="size-6 animate-spin text-muted-foreground" /></div>;

  return (
    <div className="flex flex-col items-center gap-4">
      {qrSrc && (
        <div className="rounded-xl border bg-white p-3">
          {/* eslint-disable-next-line @next/next/no-img-element -- a data: URI, not a servable asset */}
          <img src={qrSrc} alt="کد QR راه‌اندازی احراز هویت دو مرحله‌ای" width={200} height={200} />
        </div>
      )}
      <Field label="کد دستی" htmlFor="totp-secret" className="w-full">
        <div className="flex gap-1.5">
          <Input id="totp-secret" readOnly dir="ltr" value={data.secret} className="flex-1 font-mono text-xs" />
          <Button type="button" variant="outline" size="icon" onClick={copySecret} aria-label="کپی کد">
            <Copy className="size-4" />
          </Button>
        </div>
      </Field>
      <div dir="ltr">
        <InputOTP maxLength={6} value={code} onChange={setCode} nonce={nonce} aria-label="کد ۶ رقمی برنامهٔ احراز هویت">
          <InputOTPGroup>
            {Array.from({ length: 6 }, (_, i) => <InputOTPSlot key={i} index={i} className="size-10" />)}
          </InputOTPGroup>
        </InputOTP>
      </div>
      <Button className="w-full" disabled={busy || code.length < 6} onClick={enable}>
        {busy && <Loader2 className="size-4 animate-spin" />}
        فعال‌سازی
      </Button>
    </div>
  );
}

function DisablePanel({ onDisabled }: { onDisabled: () => void }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  async function disable() {
    setBusy(true);
    try {
      await api("/users/me/totp/disable", { method: "POST", json: { password } });
      toast.success("احراز هویت دو مرحله‌ای غیرفعال شد");
      onDisabled();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "رمز عبور اشتباه است");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <Field label="رمز عبور فعلی" htmlFor="totp-disable-pw">
        <Input id="totp-disable-pw" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus />
      </Field>
      <Button variant="destructive" disabled={busy || !password} onClick={disable} className="w-full">
        {busy && <Loader2 className="size-4 animate-spin" />}
        غیرفعال‌سازی
      </Button>
    </div>
  );
}

export function TotpDialog({
  open, onOpenChange, enabled, onChanged,
}: { open: boolean; onOpenChange: (o: boolean) => void; enabled: boolean; onChanged: () => void }) {
  return (
    <RingDialog
      open={open}
      onOpenChange={onOpenChange}
      icon={enabled ? KeyRound : ShieldCheck}
      title="احراز هویت دو مرحله‌ای (برنامه)"
      description={enabled
        ? "برای غیرفعال کردن، رمز عبور حسابتان را دوباره وارد کنید."
        : "با اپ Google Authenticator یا مشابه اسکن کنید، یا کد را دستی وارد کنید."}
    >
      {enabled ? (
        <DisablePanel onDisabled={() => { onChanged(); onOpenChange(false); }} />
      ) : (
        <SetupPanel onEnabled={() => { onChanged(); onOpenChange(false); }} />
      )}
    </RingDialog>
  );
}
