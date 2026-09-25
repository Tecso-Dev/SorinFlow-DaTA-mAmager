"use client";

// «لاگ اسکرپر»: the raw server log, grep'd. Gated by the `stats` permission
// on the backend (app/api/routes/stats.py) — not `scraper` — so a person
// with only `scraper` sees no way to open it, rather than a button that
// answers with a 403 (the old panel's gap, fixed here).

import { BookText, Search } from "lucide-react";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ErrorNote, ListSkeleton, RingDialog } from "@/components/panel/kit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";

const PRESETS = ["Skipping", "advertiser_type", "SMS-OTP", "rotate", "ERROR"];

export function ScraperLogDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [grep, setGrep] = useState("");

  const log = useQuery({
    queryKey: ["stats", "logs", grep],
    queryFn: () => api<{ lines: string[]; note?: string }>(`/stats/logs?lines=300${grep ? `&grep=${encodeURIComponent(grep)}` : ""}`),
    enabled: open,
  });

  return (
    <RingDialog open={open} onOpenChange={onOpenChange} icon={BookText} title="لاگ اسکرپر" wide>
      <div className="grid gap-3">
        <div className="flex flex-wrap items-center gap-1.5">
          {PRESETS.map((p) => (
            <Button key={p} type="button" size="xs" variant={grep === p ? "default" : "outline"} onClick={() => setGrep((g) => (g === p ? "" : p))}>
              {p}
            </Button>
          ))}
        </div>
        <div className="relative">
          <Search className="pointer-events-none absolute start-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input dir="ltr" aria-label="جستجوی آزاد در لاگ" placeholder="جستجوی آزاد…" value={grep} onChange={(e) => setGrep(e.target.value)} className="ps-8" />
        </div>
        <div className="max-h-[55vh] overflow-y-auto rounded-lg border bg-muted/30 p-2">
          {log.isLoading ? (
            <ListSkeleton rows={6} />
          ) : log.isError ? (
            <ErrorNote error={log.error} />
          ) : !log.data?.lines.length ? (
            <p className="p-2 text-center text-sm text-muted-foreground">چیزی یافت نشد.</p>
          ) : (
            <pre dir="ltr" className="overflow-x-auto text-start font-mono text-[11px] leading-5 whitespace-pre-wrap">
              {log.data.lines.join("\n")}
            </pre>
          )}
          {log.data?.note && <p className="mt-2 text-xs text-muted-foreground">{log.data.note}</p>}
        </div>
      </div>
    </RingDialog>
  );
}
