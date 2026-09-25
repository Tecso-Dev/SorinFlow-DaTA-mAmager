"use client";

// «اسکرپ تکی»: one Divar listing URL, scraped as a job of one — not a
// synchronous request. It gets the same OTP dialog, pacing, log and
// skipped-list bookkeeping as any other run, and shows up in the jobs
// table like one.

import { Download } from "lucide-react";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Section } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import type { ScrapeJob } from "./types";

export function SingleScrapeCard() {
  const qc = useQueryClient();
  const [url, setUrl] = useState("");

  const mutate = useMutation({
    mutationFn: (u: string) => api<ScrapeJob>("/scraper/scrape-single", { json: { url: u } }),
    onSuccess: (job) => {
      toast.success("شروع شد", `اسکرپ این ملک به جدول تسک‌ها اضافه شد: ${job.job_id}`);
      qc.invalidateQueries({ queryKey: ["scraper", "jobs"] });
      setUrl("");
    },
    onError: (e) => toast.error("خطا", e instanceof ApiError ? e.message : undefined),
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const u = url.trim();
    if (!u.includes("divar.ir/v/")) {
      toast.error("آدرس درست نیست", "آدرس باید آدرس یک آگهی دیوار باشد (شامل divar.ir/v/)");
      return;
    }
    mutate.mutate(u);
  }

  return (
    <Section title="اسکرپ تکی" bodyClassName="grid gap-3 p-4">
      <form onSubmit={submit} className="grid gap-3">
        <Input dir="ltr" type="url" aria-label="آدرس ملک در دیوار" placeholder="آدرس ملک در دیوار…" value={url} onChange={(e) => setUrl(e.target.value)} />
        <Button type="submit" variant="outline" className="w-full" disabled={mutate.isPending}>
          <Download /> اسکرپ این ملک
        </Button>
      </form>
    </Section>
  );
}
