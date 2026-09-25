"use client";

// «تعویض شماره»: move a running scrape onto another of its owner's own
// Divar numbers, without stopping it — picked up at the next safe point, or
// at once if the run is parked on a code prompt for the current number.

import { ArrowLeftRight } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { NativeSelect, RingDialog } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import { faNum } from "@/lib/format";
import { useMyCookies } from "./account-picker";
import { divarUsable, type ScrapeJob } from "./types";

export function SwitchAccountDialog({ job, onClose }: { job: ScrapeJob | null; onClose: () => void }) {
  const qc = useQueryClient();
  const cookies = useMyCookies();
  const [phone, setPhone] = useState("");

  const choices = (cookies.data?.cookies ?? []).filter((c) => divarUsable(c) && c.phone_number !== job?.divar_phone);

  const switchAccount = useMutation({
    mutationFn: () => api<{ success: boolean; message: string }>(`/scraper/jobs/${job!.job_id}/switch-account`, { json: phone ? { phone } : {} }),
    onSuccess: (r) => {
      toast.success("ثبت شد", r.message);
      qc.invalidateQueries({ queryKey: ["scraper", "jobs"] });
      onClose();
    },
    onError: (e) => toast.error("تعویض نشد", e instanceof ApiError ? e.message : undefined),
  });

  return (
    <RingDialog open={!!job} onOpenChange={(o) => !o && onClose()} icon={ArrowLeftRight} title="تعویض شمارهٔ دیوار"
      description={
        job?.divar_phone
          ? <>اسکرپ الان روی <b dir="ltr">{job.divar_phone}</b> است. بدون توقف، با شمارهٔ دیگری از شماره‌های خودتان ادامه می‌دهد.</>
          : "بدون توقف، با شمارهٔ دیگری از شماره‌های خودتان ادامه می‌دهد."
      }
    >
      {choices.length === 0 ? (
        <div className="grid gap-3 text-center text-sm text-muted-foreground">
          <p>فقط از شماره‌هایی که در پنل خودتان اضافه و تأیید شده‌اند می‌شود استفاده کرد، و شمارهٔ روشن و معتبر دیگری ندارید.</p>
          <Button asChild className="w-full"><Link href="/panel/divar">رفتن به احراز هویت دیوار</Link></Button>
          <Button variant="ghost" className="w-full" onClick={onClose}>بستن</Button>
        </div>
      ) : (
        <div className="grid gap-3">
          <NativeSelect aria-label="شمارهٔ جدید" value={phone} onChange={(e) => setPhone(e.target.value)}>
            <option value="">خودکار — کم‌مصرف‌ترین شمارهٔ دیگر من</option>
            {choices.map((c) => (
              <option key={c.id} value={c.phone_number}>{c.phone_number} — {faNum(c.reveals || 0)} افشا</option>
            ))}
          </NativeSelect>
          <p className="text-xs text-muted-foreground">
            فقط شماره‌های تأییدشدهٔ خودتان در این فهرست‌اند. اگر گوشی شمارهٔ فعلی در دسترس نیست، بهتر است آن را در فرم اسکرپر خاموش کنید تا دوباره انتخاب نشود.
          </p>
          <Button className="w-full" disabled={switchAccount.isPending} onClick={() => switchAccount.mutate()}>تعویض و ادامه</Button>
          <Button variant="ghost" className="w-full" onClick={onClose}>انصراف</Button>
        </div>
      )}
    </RingDialog>
  );
}
