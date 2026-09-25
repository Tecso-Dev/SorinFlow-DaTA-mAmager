"use client";

// «وارد کردن دسته‌ای»: a multi-line textarea, one proxy per line, three
// accepted formats. Rows land untested (is_working=false) until a real
// probe confirms them — refused rows never reach the table at all.

import { Loader2, Upload } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Field, RingDialog } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import { faNum } from "@/lib/format";
import type { ImportResult } from "./types";

const PLACEHOLDER = `هر پراکسی در یک خط، به یکی از این سه شکل:
1.2.3.4:8080
1.2.3.4:8080:user:pass
http://user:pass@1.2.3.4:8080`;

export function ImportProxiesDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [text, setText] = useState("");

  const run = useMutation({
    mutationFn: () => api<ImportResult>("/proxies/import", { json: { proxy_list: text, test: true } }),
    onSuccess: (r) => {
      toast.success(
        "وارد کردن انجام شد",
        `${faNum(r.imported)} افزوده شد، ${faNum(r.skipped)} تکراری، ${faNum(r.refused)} آدرس داخلی رد شد` +
          (r.tested ? `، ${faNum(r.tested.working)} از ${faNum(r.tested.tested)} تست‌شده فعال بود` : ""),
      );
      qc.invalidateQueries({ queryKey: ["proxies"] });
      setText("");
      onOpenChange(false);
    },
    onError: (e) => toast.error("وارد کردن ناموفق بود", e instanceof ApiError ? e.message : undefined),
  });

  return (
    <RingDialog
      open={open}
      onOpenChange={(o) => {
        if (!o) setText("");
        onOpenChange(o);
      }}
      icon={Upload}
      title="وارد کردن دسته‌ای"
      description="لیست پراکسی‌ها را در ادامه بچسبانید؛ آدرس‌های داخلی رد و ردیف‌های تکراری رد می‌شوند."
      footer={
        <>
          <Button
            className="w-full shadow-[0_8px_24px_-8px_rgb(99_102_241/0.8)]"
            disabled={run.isPending || !text.trim()}
            onClick={() => run.mutate()}
          >
            {run.isPending && <Loader2 className="animate-spin" />} وارد کردن
          </Button>
          <Button variant="ghost" className="w-full" onClick={() => onOpenChange(false)}>انصراف</Button>
        </>
      }
    >
      <Field label="لیست پراکسی‌ها" htmlFor="pxy-import" hint="ip:port یا ip:port:user:pass یا scheme://[user:pass@]host:port">
        <Textarea
          id="pxy-import"
          dir="ltr"
          rows={7}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={PLACEHOLDER}
          className="font-mono text-xs"
        />
      </Field>
    </RingDialog>
  );
}
