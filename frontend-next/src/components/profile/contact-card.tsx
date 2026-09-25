"use client";

// تماس و تأیید — email + phone change-then-verify, and the read-only list of
// Divar numbers this account owns (adding/managing them happens in «احراز
// هویت دیوار»; this card only shows the result).

import { CheckCircle2, KeyRound, Loader2, Mail, Phone, Smartphone } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { InputOTP, InputOTPGroup, InputOTPSlot } from "@/components/ui/input-otp";
import { Empty, ListSkeleton, Section, ToneBadge } from "@/components/panel/kit";
import { Reveal } from "@/components/viz";
import { toast } from "@/components/toaster";
import { useNonce } from "@/components/nonce";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { SESSION_KEY, type User } from "@/lib/session";

type DivarCookie = {
  id: number; phone_number: string; is_valid: boolean; reveals: number;
};

function VerifiedBadge({ set, verified }: { set: boolean; verified: boolean }) {
  if (!set) return <ToneBadge tone="neutral">ثبت نشده</ToneBadge>;
  return <ToneBadge tone={verified ? "success" : "warning"}>{verified ? "تأیید شده" : "تأیید نشده"}</ToneBadge>;
}

function ChangeChannel({
  kind, current, verified, onSent, sendCode, confirmCode,
}: {
  kind: "email" | "phone";
  current: string | null;
  verified: boolean;
  onSent: () => void;
  sendCode: (value: string) => Promise<{ sent: boolean; verified: boolean; message: string }>;
  confirmCode: (code: string) => Promise<{ verified: boolean; message: string }>;
}) {
  const nonce = useNonce();
  const [value, setValue] = useState("");
  const [code, setCode] = useState("");
  const [stage, setStage] = useState<"idle" | "sent">("idle");
  const [busy, setBusy] = useState(false);

  async function send() {
    setBusy(true);
    try {
      const r = await sendCode(value.trim());
      if (r.verified) {
        toast.success(r.message);
        onSent();
        return;
      }
      setStage("sent");
      toast.success(r.message);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "ارسال نشد");
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    setBusy(true);
    try {
      const r = await confirmCode(code);
      toast.success(r.message);
      setStage("idle");
      setValue("");
      setCode("");
      onSent();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "کد نادرست است");
    } finally {
      setBusy(false);
    }
  }

  const Icon = kind === "email" ? Mail : Phone;

  return (
    <div className="flex flex-col gap-2 rounded-xl border p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Icon className="size-4 shrink-0 text-muted-foreground" />
          <span dir="ltr" className="truncate text-sm font-semibold">{current || "—"}</span>
        </div>
        <VerifiedBadge set={!!current} verified={verified} />
      </div>
      {stage === "idle" ? (
        <div className="flex gap-2">
          <Input
            dir="ltr"
            placeholder={kind === "email" ? "ایمیل تازه (اختیاری)" : "شمارهٔ تازه (اختیاری)"}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="flex-1"
          />
          <Button size="sm" disabled={busy || (!value.trim() && verified)} onClick={send}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            {current && !verified && !value.trim() ? "ارسال کد" : "تغییر و ارسال کد"}
          </Button>
        </div>
      ) : (
        <div className="flex flex-col items-start gap-2">
          <p className="text-xs text-muted-foreground">کد ارسال‌شده را وارد کنید</p>
          <div dir="ltr">
            <InputOTP maxLength={6} value={code} onChange={setCode} nonce={nonce} aria-label="کد تأیید">
              <InputOTPGroup>
                {Array.from({ length: 6 }, (_, i) => <InputOTPSlot key={i} index={i} />)}
              </InputOTPGroup>
            </InputOTP>
          </div>
          <div className="flex gap-2">
            <Button size="sm" disabled={busy || code.length < 6} onClick={confirm}>
              {busy && <Loader2 className="size-4 animate-spin" />}
              تأیید کد
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setStage("idle")}>انصراف</Button>
          </div>
        </div>
      )}
    </div>
  );
}

export function ContactCard({ me }: { me: User }) {
  const qc = useQueryClient();
  const divar = useQuery({
    queryKey: ["profile", "divar-cookies"],
    queryFn: () => api<{ cookies: DivarCookie[] }>("/auth/cookies?mine=1"),
  });

  async function refresh() {
    await qc.invalidateQueries({ queryKey: SESSION_KEY });
  }

  return (
    <Reveal delay={0.15}>
      <Section title="تماس و تأیید">
        <div className="flex flex-col gap-3">
          <ChangeChannel
            kind="email"
            current={me.email}
            verified={me.email_verified}
            onSent={refresh}
            sendCode={(v) => api("/users/me/email/request", { json: v ? { email: v } : {} })}
            confirmCode={(code) => api("/users/me/email/verify", { json: { code } })}
          />
          <ChangeChannel
            kind="phone"
            current={me.phone}
            verified={me.phone_verified}
            onSent={refresh}
            sendCode={(v) => api("/users/me/phone/request", { json: v ? { phone: v } : {} })}
            confirmCode={(code) => api("/users/me/phone/verify", { json: { code } })}
          />

          <div className="mt-2 border-t pt-3">
            <div className="mb-2 flex items-center gap-2 text-sm font-bold">
              <Smartphone className="size-4 text-muted-foreground" />
              شماره‌های دیوار من
            </div>
            {divar.isPending ? (
              <ListSkeleton rows={2} />
            ) : divar.isError ? (
              <Empty>بارگیری ناموفق بود</Empty>
            ) : divar.data.cookies.length === 0 ? (
              <Empty icon={KeyRound}>هنوز با هیچ شماره‌ای وارد دیوار نشده‌اید — از «احراز هویت دیوار» اضافه کنید.</Empty>
            ) : (
              <ul className="flex flex-col gap-1.5">
                {divar.data.cookies.map((c) => (
                  <li key={c.id} className="flex items-center justify-between rounded-lg border px-3 py-2 text-sm">
                    <span dir="ltr" className="font-semibold">{c.phone_number}</span>
                    <span className="flex items-center gap-1.5">
                      {c.phone_number === me.divar_phone && <ToneBadge tone="primary">پیش‌فرض</ToneBadge>}
                      <ToneBadge tone={c.is_valid ? "success" : "neutral"}>
                        {c.is_valid ? <CheckCircle2 className="size-3" /> : null}
                        {c.is_valid ? "معتبر" : "منقضی"}
                      </ToneBadge>
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <Link href="/panel/divar" className="mt-2 inline-block text-xs font-semibold text-primary hover:underline">
              افزودن شماره در «احراز هویت دیوار» ←
            </Link>
          </div>
        </div>
      </Section>
    </Reveal>
  );
}
