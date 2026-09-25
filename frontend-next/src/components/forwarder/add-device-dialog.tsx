"use client";

// «افزودن گوشی»: one proper form — name, first SIM (required), second SIM
// (optional, for a dual-SIM phone) — replacing the old panel's three
// sequential window.prompt() calls. The server hands back the device's full
// secret exactly once; this dialog is the one place besides the install
// guide that is allowed to show it.

import { Check, Copy, KeyRound, Loader2, Smartphone } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Field, RingDialog } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import { parseDigits } from "@/lib/format";
import type { Device } from "./types";

type Form = { label: string; sim_phone: string; sim_phone2: string };
const EMPTY: Form = { label: "", sim_phone: "", sim_phone2: "" };

export function AddDeviceDialog({
  open, onOpenChange, onOpenGuide,
}: { open: boolean; onOpenChange: (o: boolean) => void; onOpenGuide: (deviceId: number) => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState<Form>(EMPTY);
  const [tried, setTried] = useState(false);
  const [created, setCreated] = useState<Device | null>(null);
  const [copied, setCopied] = useState(false);
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((s) => ({ ...s, [k]: v }));

  function reset() {
    setF(EMPTY);
    setTried(false);
    setCreated(null);
    setCopied(false);
  }

  const add = useMutation({
    mutationFn: () =>
      // phone-gated on the server (require_verified_phone) — api() shows the
      // verification dialog itself and retries this same call once it passes.
      api<Device>("/forwarder/devices", {
        json: {
          label: f.label.trim() || null,
          sim_phone: parseDigits(f.sim_phone).trim(),
          sim_phone2: parseDigits(f.sim_phone2).trim() || null,
        },
      }),
    onSuccess: (d) => {
      toast.success("گوشی ثبت شد");
      qc.invalidateQueries({ queryKey: ["forwarder", "devices"] });
      setCreated(d);
    },
    // 400 (two SIMs, one number), 409 (SIM already yours elsewhere) and 403
    // (someone else's number, or someone else's Divar login) all arrive as
    // ApiError.message straight from the server, already in Persian.
    onError: (e) => toast.error("ثبت نشد", e instanceof ApiError ? e.message : undefined),
  });

  async function copySecret() {
    if (!created?.secret) return;
    try {
      await navigator.clipboard.writeText(created.secret);
      setCopied(true);
      toast.success("رمز کپی شد");
    } catch {
      toast.error("کپی نشد", "دسترسی به کلیپ‌بورد رد شد");
    }
  }

  const simErr = tried && !f.sim_phone.trim() ? "شمارهٔ سیم اول الزامی است" : undefined;

  return (
    <RingDialog
      open={open}
      onOpenChange={(o) => {
        if (!o) reset();
        onOpenChange(o);
      }}
      icon={created ? KeyRound : Smartphone}
      title={created ? "رمز این دستگاه" : "افزودن گوشی"}
      description={
        created
          ? "این رمز فقط همین یک‌بار کامل نشان داده می‌شود؛ جایی دیگر در پنل — حتی همین فهرست — کامل دیده نمی‌شود."
          : "نام دلخواه و شمارهٔ سیمی که در این گوشی است. اگر گوشی دو سیم‌کارته است، سیم دوم را هم وارد کنید."
      }
      wide
      footer={
        created ? (
          <>
            <Button className="w-full" onClick={() => { onOpenGuide(created.id); onOpenChange(false); }}>
              باز کردن راهنمای نصب
            </Button>
            <Button variant="ghost" className="w-full" onClick={() => onOpenChange(false)}>بستن</Button>
          </>
        ) : (
          <>
            <Button
              className="w-full shadow-[0_8px_24px_-8px_rgb(99_102_241/0.8)]"
              disabled={add.isPending}
              onClick={() => {
                setTried(true);
                if (f.sim_phone.trim()) add.mutate();
              }}
            >
              {add.isPending && <Loader2 className="animate-spin" />} ثبت گوشی
            </Button>
            <Button variant="ghost" className="w-full" onClick={() => onOpenChange(false)}>انصراف</Button>
          </>
        )
      }
    >
      {created ? (
        <div className="grid gap-3">
          <div className="rounded-xl border bg-muted/30 p-3">
            <div className="text-xs text-muted-foreground">{created.label || "گوشی من"}</div>
            <div dir="ltr" className="mt-1 break-all text-start font-mono text-sm tabular">{created.secret}</div>
          </div>
          <Button type="button" variant="outline" onClick={copySecret}>
            {copied ? <Check /> : <Copy />} کپی رمز
          </Button>
          <p className="text-xs leading-6 text-muted-foreground">
            راهنمای نصب همین رمز را دوباره نشان می‌دهد؛ اگر بعداً رمز را گم کردید، «کلید تازه» بزنید.
          </p>
        </div>
      ) : (
        <div className="grid gap-3">
          <Field label="نام گوشی" htmlFor="fw-label" hint="اختیاری — مثلاً «شیائومی سبحان»">
            <Input id="fw-label" value={f.label} onChange={(e) => set("label", e.target.value)} placeholder="گوشی من" />
          </Field>
          <Field label="سیم اول *" htmlFor="fw-sim1" error={simErr}>
            <Input id="fw-sim1" dir="ltr" inputMode="numeric" aria-invalid={!!simErr || undefined} value={f.sim_phone} onChange={(e) => set("sim_phone", e.target.value)} placeholder="09xxxxxxxxx" className="text-end tabular" />
          </Field>
          <Field label="سیم دوم" htmlFor="fw-sim2" hint="اختیاری — فقط برای گوشی دو سیم‌کارته">
            <Input id="fw-sim2" dir="ltr" inputMode="numeric" value={f.sim_phone2} onChange={(e) => set("sim_phone2", e.target.value)} placeholder="09xxxxxxxxx" className="text-end tabular" />
          </Field>
        </div>
      )}
    </RingDialog>
  );
}
