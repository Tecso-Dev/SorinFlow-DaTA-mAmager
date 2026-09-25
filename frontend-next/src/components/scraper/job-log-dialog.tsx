"use client";

// «گزارش این اسکرپ»: what the scraper decided about every listing it
// looked at, in order — the record scraper.log itself cannot give back once
// two runs interleave in it or it has rotated past a week.

import { ScrollText, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ErrorNote, ListSkeleton, RingDialog } from "@/components/panel/kit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { faDate, faNum } from "@/lib/format";
import type { JobEventsResponse } from "./types";

const PRESETS = ["Skipping", "advertiser_type", "SMS-OTP", "rotate"] as const;

const LEVEL_TONE: Record<string, string> = { error: "text-destructive", warning: "text-warning", info: "text-muted-foreground" };

export function JobLogDialog({ jobId, onClose }: { jobId: string | null; onClose: () => void }) {
  const [search, setSearch] = useState("");
  const [level, setLevel] = useState<string | null>(null);

  const events = useQuery({
    queryKey: ["scraper", "job-events", jobId, level],
    queryFn: () => api<JobEventsResponse>(`/scraper/jobs/${jobId}/events?limit=500${level ? `&level=${level}` : ""}`),
    enabled: !!jobId,
  });

  const filtered = useMemo(() => {
    const items = events.data?.items ?? [];
    const q = search.trim().toLowerCase();
    if (!q) return items;
    return items.filter((e) => `${e.message} ${e.stage ?? ""} ${JSON.stringify(e.details)}`.toLowerCase().includes(q));
  }, [events.data, search]);

  function preset(term: string) {
    if (term === "ERROR") {
      setLevel(level === "error" ? null : "error");
      setSearch("");
    } else {
      setLevel(null);
      setSearch((s) => (s === term ? "" : term));
    }
  }

  return (
    <RingDialog open={!!jobId} onOpenChange={(o) => !o && onClose()} icon={ScrollText} title="گزارش اسکرپ" wide>
      <div className="grid gap-3">
        <div className="flex flex-wrap items-center gap-1.5">
          {[...PRESETS, "ERROR"].map((p) => (
            <Button
              key={p}
              type="button"
              size="xs"
              variant={search === p || (p === "ERROR" && level === "error") ? "default" : "outline"}
              onClick={() => preset(p)}
            >
              {p}
            </Button>
          ))}
        </div>
        <div className="relative">
          <Search className="pointer-events-none absolute start-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input dir="ltr" aria-label="جستجوی آزاد در گزارش" placeholder="جستجوی آزاد…" value={search} onChange={(e) => setSearch(e.target.value)} className="ps-8" />
        </div>
        <div className="max-h-[50vh] overflow-y-auto rounded-lg border">
          {events.isLoading ? (
            <div className="p-4"><ListSkeleton rows={5} /></div>
          ) : events.isError ? (
            <div className="p-4"><ErrorNote error={events.error} /></div>
          ) : filtered.length === 0 ? (
            <p className="p-4 text-center text-sm text-muted-foreground">چیزی یافت نشد.</p>
          ) : (
            <ul className="divide-y text-xs">
              {filtered.map((e) => (
                <li key={e.id} className="grid gap-0.5 p-2.5">
                  <div className="flex items-center gap-2">
                    <span className={LEVEL_TONE[e.level] ?? "text-foreground"}>{e.message}</span>
                  </div>
                  <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                    {e.stage && <span>{e.stage}</span>}
                    {e.created_at && <span dir="ltr" className="tabular">{faDate(new Date(e.created_at), { hour: "2-digit", minute: "2-digit", month: "2-digit", day: "2-digit" })}</span>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
        <p className="text-[11px] text-muted-foreground">{faNum(filtered.length)} از {faNum(events.data?.count ?? 0)} رویداد</p>
      </div>
    </RingDialog>
  );
}
