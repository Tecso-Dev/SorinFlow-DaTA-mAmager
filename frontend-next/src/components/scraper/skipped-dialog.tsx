"use client";

// «آگهی‌های ردشده»: the listings this run saw and did not save, with why —
// filterable by reason, and re-scraped in bulk as a single new job (the
// same links the finish line already counted, handed back as a run).

import { ExternalLink, ListFilter, RefreshCw } from "lucide-react";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Empty, ErrorNote, ListSkeleton, NativeSelect, RingDialog } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import { faDate, faNum } from "@/lib/format";
import type { ScrapeJob, SkippedResponse } from "./types";

export function SkippedDialog({ job, onClose }: { job: ScrapeJob | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [reason, setReason] = useState("");

  const skipped = useQuery({
    queryKey: ["scraper", "skipped", job?.job_id, reason],
    queryFn: () => api<SkippedResponse>(`/scraper/jobs/${job!.job_id}/skipped?limit=1000${reason ? `&reason=${encodeURIComponent(reason)}` : ""}`),
    enabled: !!job,
  });

  const rescrape = useMutation({
    mutationFn: (urls: string[]) => api(`/scraper/rescrape`, { json: { urls, label: `بازاسکرپ — ${job?.category_name || job?.city_name || ""}` } }),
    onSuccess: () => {
      toast.success("شروع شد", "بازاسکرپ به‌عنوان یک تسک تازه اضافه شد");
      qc.invalidateQueries({ queryKey: ["scraper", "jobs"] });
      onClose();
    },
    onError: (e) => toast.error("خطا", e instanceof ApiError ? e.message : undefined),
  });

  const items = skipped.data?.items ?? [];
  const reasons = Object.entries(skipped.data?.by_reason ?? {});

  return (
    <RingDialog open={!!job} onOpenChange={(o) => !o && onClose()} icon={ListFilter} title="آگهی‌های ردشده" wide>
      <div className="grid gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <NativeSelect aria-label="فیلتر دلیل" className="w-auto min-w-48" value={reason} onChange={(e) => setReason(e.target.value)}>
            <option value="">همهٔ دلیل‌ها{skipped.data ? ` (${faNum(skipped.data.count)})` : ""}</option>
            {reasons.map(([k, v]) => (
              <option key={k} value={k}>{v.label} ({faNum(v.count)})</option>
            ))}
          </NativeSelect>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="ms-auto"
            disabled={!items.length || rescrape.isPending}
            onClick={() => rescrape.mutate(items.map((i) => i.url))}
          >
            <RefreshCw /> بازاسکرپ همه ({faNum(items.length)})
          </Button>
        </div>
        <div className="max-h-[50vh] overflow-y-auto rounded-lg border">
          {skipped.isLoading ? (
            <div className="p-4"><ListSkeleton rows={5} /></div>
          ) : skipped.isError ? (
            <div className="p-4"><ErrorNote error={skipped.error} /></div>
          ) : items.length === 0 ? (
            <div className="p-4"><Empty>هیچ آگهی ردشده‌ای با این فیلتر نیست.</Empty></div>
          ) : (
            <ul className="divide-y text-xs">
              {items.map((i) => (
                <li key={i.id} className="flex items-start gap-2 p-2.5">
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">{i.title || i.divar_id || i.url}</div>
                    <div className="text-[11px] text-muted-foreground">
                      {i.reason_label}
                      {i.detail ? ` — ${i.detail}` : ""}
                      {i.created_at && <span dir="ltr" className="tabular"> · {faDate(new Date(i.created_at), { hour: "2-digit", minute: "2-digit", month: "2-digit", day: "2-digit" })}</span>}
                    </div>
                  </div>
                  <a href={i.url} target="_blank" rel="noopener noreferrer" className="shrink-0 text-muted-foreground hover:text-primary" aria-label="باز کردن آگهی">
                    <ExternalLink className="size-4" />
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </RingDialog>
  );
}
