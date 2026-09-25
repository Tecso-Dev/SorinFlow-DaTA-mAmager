"use client";

// «افزودن پراکسی»: address, port, protocol, optional username/password.
// The server rejects a private/internal address (400, net_guard's Persian
// message shown as-is) and a duplicate address+port (400).

import { Loader2, ShieldPlus } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Field, NativeSelect, RingDialog } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import { parseDigits } from "@/lib/format";
import type { Proxy } from "./types";

type Form = { address: string; port: string; protocol: string; username: string; password: string };
const EMPTY: Form = { address: "", port: "", protocol: "http", username: "", password: "" };

export function AddProxyDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState<Form>(EMPTY);
  const [tried, setTried] = useState(false);
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((s) => ({ ...s, [k]: v }));

  const port = Number(parseDigits(f.port));
  const addressOk = f.address.trim().length > 0;
  const portOk = f.port.trim() !== "" && Number.isInteger(port) && port > 0 && port <= 65535;

  const save = useMutation({
    mutationFn: () =>
      api<Proxy>("/proxies", {
        json: {
          address: f.address.trim(),
          port,
          protocol: f.protocol,
          username: f.username.trim() || null,
          password: f.password.trim() || null,
        },
      }),
    onSuccess: () => {
      toast.success("پراکسی اضافه شد");
      qc.invalidateQueries({ queryKey: ["proxies"] });
      setF(EMPTY);
      setTried(false);
      onOpenChange(false);
    },
    // net_guard's Persian message (آدرس داخلی/خصوصی، یا تکراری) shown as-is
    onError: (e) => toast.error("افزوده نشد", e instanceof ApiError ? e.message : undefined),
  });

  const addrErr = tried && !addressOk ? "آدرس الزامی است" : undefined;
  const portErr = tried && !portOk ? "پورت باید بین ۱ تا ۶۵۵۳۵ باشد" : undefined;

  return (
    <RingDialog
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          setF(EMPTY);
          setTried(false);
        }
        onOpenChange(o);
      }}
      icon={ShieldPlus}
      title="افزودن پراکسی"
      description="آدرس باید عمومی باشد؛ آدرس‌های داخلی و خصوصی پذیرفته نمی‌شوند."
      footer={
        <>
          <Button
            className="w-full shadow-[0_8px_24px_-8px_rgb(99_102_241/0.8)]"
            disabled={save.isPending}
            onClick={() => {
              setTried(true);
              if (addressOk && portOk) save.mutate();
            }}
          >
            {save.isPending && <Loader2 className="animate-spin" />} افزودن پراکسی
          </Button>
          <Button variant="ghost" className="w-full" onClick={() => onOpenChange(false)}>انصراف</Button>
        </>
      }
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="آدرس IP یا نام" htmlFor="pxy-address" className="sm:col-span-2" error={addrErr}>
          <Input
            id="pxy-address"
            dir="ltr"
            value={f.address}
            aria-invalid={!!addrErr || undefined}
            onChange={(e) => set("address", e.target.value)}
            placeholder="192.168.1.1"
            className="text-end tabular"
          />
        </Field>
        <Field label="پورت" htmlFor="pxy-port" error={portErr}>
          <Input
            id="pxy-port"
            dir="ltr"
            inputMode="numeric"
            value={f.port}
            aria-invalid={!!portErr || undefined}
            onChange={(e) => set("port", e.target.value)}
            placeholder="8080"
            className="text-end tabular"
          />
        </Field>
        <Field label="پروتکل" htmlFor="pxy-protocol">
          <NativeSelect id="pxy-protocol" value={f.protocol} onChange={(e) => set("protocol", e.target.value)}>
            <option value="http">HTTP</option>
            <option value="https">HTTPS</option>
            <option value="socks5">SOCKS5</option>
          </NativeSelect>
        </Field>
        <Field label="نام کاربری" htmlFor="pxy-username" hint="اختیاری">
          <Input id="pxy-username" dir="ltr" value={f.username} onChange={(e) => set("username", e.target.value)} />
        </Field>
        <Field label="رمز عبور" htmlFor="pxy-password" hint="اختیاری">
          <Input id="pxy-password" dir="ltr" type="password" value={f.password} onChange={(e) => set("password", e.target.value)} />
        </Field>
      </div>
    </RingDialog>
  );
}
