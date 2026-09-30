"use client";

// «ویرایش سیم‌کارت»: PATCH /forwarder/devices/{id} with only sim_phone /
// sim_phone2 — everything else about the device stays put. The warning that
// the QR/guide needs a rescan lives in the dialog body, not a toast, so it
// cannot be missed the way a toast can.

import { Loader2, PenLine, TriangleAlert } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Field, RingDialog } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import { parseDigits } from "@/lib/format";
import type { Device } from "./types";

export function EditSimDialog({ device, onOpenChange }: { device: Device | null; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [sim1, setSim1] = useState("");
  const [sim2, setSim2] = useState("");
  // Re-seed the fields from the row whenever a different device is opened —
  // done during render, not an effect, so it cannot lag a frame behind.
  const [seenId, setSeenId] = useState<number | null>(null);
  if (device && device.id !== seenId) {
    setSeenId(device.id);
    setSim1(device.sim_phone ?? "");
    setSim2(device.sim_phone2 ?? "");
  }

  const save = useMutation({
    mutationFn: () =>
      api<Device>(`/forwarder/devices/${device!.id}`, {
        method: "PATCH",
        json: { sim_phone: parseDigits(sim1).trim(), sim_phone2: parseDigits(sim2).trim() },
      }),
    onSuccess: () => {
      toast.success("شمارهٔ سیم به‌روز شد");
      qc.invalidateQueries({ queryKey: ["forwarder", "devices"] });
      qc.invalidateQueries({ queryKey: ["forwarder", "config", device?.id] });
      onOpenChange(false);
    },
    onError: (e) => toast.error("ذخیره نشد", e instanceof ApiError ? e.message : undefined),
  });

  return (
    <RingDialog
      open={!!device}
      onOpenChange={onOpenChange}
      icon={PenLine}
      title="ویرایش سیم‌کارت"
      description={device ? `گوشی «${device.label || device.device_id}»` : undefined}
      footer={
        <>
          <Button className="w-full" disabled={save.isPending} onClick={() => save.mutate()}>
            {save.isPending && <Loader2 className="animate-spin" />} ذخیره
          </Button>
          <Button variant="ghost" className="w-full" onClick={() => onOpenChange(false)}>انصراف</Button>
        </>
      }
    >
      <div className="grid gap-3">
        <Field label="سیم اول" htmlFor="fw-edit-sim1">
          <Input id="fw-edit-sim1" dir="ltr" inputMode="numeric" value={sim1} onChange={(e) => setSim1(e.target.value)} placeholder="09xxxxxxxxx" className="text-end tabular" />
        </Field>
        <Field label="سیم دوم" htmlFor="fw-edit-sim2" hint="خالی بگذارید تا حذف شود">
          <Input id="fw-edit-sim2" dir="ltr" inputMode="numeric" value={sim2} onChange={(e) => setSim2(e.target.value)} placeholder="09xxxxxxxxx" className="text-end tabular" />
        </Field>
        <div className="flex items-start gap-2 rounded-xl border border-warning/30 bg-warning/10 p-3 text-xs leading-6 text-foreground">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
          بعد از این تغییر، کد QR و راهنمای نصب باید دوباره در گوشی اسکن/وارد شود؛ تا آن موقع کدهای همین شماره درست فرستاده نمی‌شوند.
        </div>
      </div>
    </RingDialog>
  );
}
