"use client";

// بکاپ کامل (DR) — the host-level nightly bundle (DB + secrets + Divar
// sessions + browser profiles + SSL). «همین حالا» only drops a request file
// for the host's systemd unit to pick up, so a run is polled rather than
// awaited; «تست همهٔ راه‌ها» reuses the same diagnose endpoint as the
// Telegram backup card, across every route at once.

import { HardDriveDownload, Loader2, Radar } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Section, ToneBadge } from "@/components/panel/kit";
import { Reveal } from "@/components/viz";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { faDate, faNum } from "@/lib/format";

type DrStatus = {
  last_run: { at: string; ok: boolean } | null;
  history: { at: string; ok: boolean }[];
  last_alert: string | null;
  requested: boolean;
  undelivered: string[];
  schedule_fa: string;
};
type DiagRow = { route: string; target: string; ok: boolean; http: number | null; error: string | null; ms: number };

const QKEY = ["users", "dr-status"] as const;
const POLL_MS = 4000;
const MAX_POLLS = 30;

export function DrCard() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: QKEY, queryFn: () => api<DrStatus>("/backup/dr") });
  const [running, setRunning] = useState(false);
  const [diag, setDiag] = useState<DiagRow[] | null>(null);
  const [diagBusy, setDiagBusy] = useState(false);
  const pollsRef = useRef(0);

  useEffect(() => {
    if (!running) return;
    const id = setInterval(async () => {
      pollsRef.current += 1;
      const r = await qc.fetchQuery({ queryKey: QKEY, queryFn: () => api<DrStatus>("/backup/dr") });
      if (!r.requested || pollsRef.current >= MAX_POLLS) {
        setRunning(false);
        if (!r.requested) toast.success("بکاپ کامل تمام شد");
      }
    }, POLL_MS);
    return () => clearInterval(id);
  }, [running, qc]);

  async function runNow() {
    try {
      await api("/backup/dr/run", { method: "POST" });
      pollsRef.current = 0;
      setRunning(true);
      await qc.invalidateQueries({ queryKey: QKEY });
      toast.success("درخواست بکاپ کامل ثبت شد");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "ثبت درخواست ناموفق بود");
    }
  }

  async function diagnose() {
    setDiagBusy(true);
    try {
      const r = await api<{ rows: DiagRow[] }>("/backup/diagnose", { method: "POST" });
      setDiag(r.rows);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "آزمایش ناموفق بود");
    } finally {
      setDiagBusy(false);
    }
  }

  const busy = running || q.data?.requested;

  return (
    <Reveal delay={0.15}>
      <Section title="بکاپ کامل (DR)" hint={q.data?.schedule_fa}>
        {q.isPending ? (
          <div className="h-20 animate-pulse rounded-lg bg-muted/40" />
        ) : (
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-muted-foreground">آخرین اجرا:</span>
              {q.data?.last_run ? (
                <>
                  <span className="font-semibold">{faDate(new Date(q.data.last_run.at), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
                  <ToneBadge tone={q.data.last_run.ok ? "success" : "danger"}>{q.data.last_run.ok ? "موفق" : "ناموفق"}</ToneBadge>
                </>
              ) : <span>—</span>}
            </div>
            {busy && (
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="size-3.5 animate-spin" /> در انتظار سرور — چند دقیقه طول می‌کشد
              </div>
            )}
            {q.data?.undelivered && q.data.undelivered.length > 0 && (
              <p className="text-xs text-warning">{faNum(q.data.undelivered.length)} بستهٔ ارسال‌نشده روی سرور مانده است.</p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button size="sm" className="gap-1.5" disabled={!!busy} onClick={runNow}>
                {busy ? <Loader2 className="size-4 animate-spin" /> : <HardDriveDownload className="size-4" />}
                همین حالا
              </Button>
              <Button size="sm" variant="outline" className="gap-1.5" disabled={diagBusy} onClick={diagnose}>
                {diagBusy ? <Loader2 className="size-4 animate-spin" /> : <Radar className="size-4" />}
                تست همهٔ راه‌ها
              </Button>
            </div>

            {diag && (
              <div className="overflow-x-auto rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow className="hover:bg-transparent">
                      <TableHead>راه</TableHead>
                      <TableHead>مقصد</TableHead>
                      <TableHead>نتیجه</TableHead>
                      <TableHead>زمان</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {diag.map((r, i) => (
                      <TableRow key={i}>
                        <TableCell>{r.route}</TableCell>
                        <TableCell dir="ltr" className="max-w-[160px] truncate text-xs">{r.target}</TableCell>
                        <TableCell>
                          <ToneBadge tone={r.ok ? "success" : "danger"}>{r.ok ? "موفق" : r.error || "ناموفق"}</ToneBadge>
                        </TableCell>
                        <TableCell className="tabular">{faNum(r.ms)}ms</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </div>
        )}
      </Section>
    </Reveal>
  );
}
