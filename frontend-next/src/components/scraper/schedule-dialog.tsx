"use client";

// «هر روز خودکار اجرا شود»: name + hour, saved as the current form's exact
// filters. POST /scraper/schedules needs a verified phone; api() pops the
// shell's gate on its own.

import { AlarmClock } from "lucide-react";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Field, RingDialog } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import { faNum } from "@/lib/format";
import type { ScrapeConfig } from "./types";

export function SaveScheduleDialog({
  open, onOpenChange, config, cityLabel, categoryLabel,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  config: ScrapeConfig | null;
  cityLabel: string;
  categoryLabel: string;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [time, setTime] = useState("08:00");
  const [error, setError] = useState("");
  // resets the form the moment the dialog opens, adjusted during render
  const [seenOpen, setSeenOpen] = useState(false);
  if (open !== seenOpen) {
    setSeenOpen(open);
    if (open) {
      setName(cityLabel && categoryLabel ? `${cityLabel} — ${categoryLabel}` : "");
      setTime("08:00");
      setError("");
    }
  }

  const save = useMutation({
    mutationFn: async () => {
      if (!config) throw new Error("no config");
      const [h, m] = time.split(":").map(Number);
      return api("/scraper/schedules", {
        json: { name: name.trim(), config, hour: h, minute: m, enabled: true },
      });
    },
    onSuccess: () => {
      toast.success("ذخیره شد", `هر روز ساعت ${time} اجرا می‌شود`);
      qc.invalidateQueries({ queryKey: ["scraper", "schedules"] });
      onOpenChange(false);
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "ذخیره نشد"),
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      setError("اسمی برای زمان‌بندی بنویسید");
      return;
    }
    setError("");
    save.mutate();
  }

  return (
    <RingDialog
      open={open}
      onOpenChange={onOpenChange}
      icon={AlarmClock}
      title="اجرای روزانه"
      description={
        <>
          هر روز <b>{cityLabel || config?.city}</b> / <b>{categoryLabel || config?.category}</b>
          {config?.max_items ? ` تا ${faNum(config.max_items)} آگهی` : ""} با همین فیلترها اجرا می‌شود — با حساب‌های دیوار خودتان.
          {!config?.max_age_hours && (
            <span className="mt-1 block text-warning">چون «حداکثر سن آگهی» خالی است، فقط آگهی‌های ۲۴ ساعت اخیر گرفته می‌شود.</span>
          )}
        </>
      }
    >
      <form onSubmit={submit} className="grid gap-3">
        <Field label="اسم زمان‌بندی" htmlFor="sched-name">
          <Input id="sched-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="مثلاً اجارهٔ آپارتمان ارومیه" />
        </Field>
        <Field label="ساعت اجرا (به وقت تهران)" htmlFor="sched-time" error={error || undefined}>
          <Input id="sched-time" type="time" dir="ltr" value={time} onChange={(e) => setTime(e.target.value)} className="tabular" />
        </Field>
        <Button type="submit" className="w-full" disabled={save.isPending || !config}>
          ذخیره
        </Button>
        <Button type="button" variant="ghost" className="w-full" onClick={() => onOpenChange(false)}>
          انصراف
        </Button>
      </form>
    </RingDialog>
  );
}
