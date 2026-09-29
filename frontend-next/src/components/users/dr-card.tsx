"use client";

// بکاپ کامل (DR) — the host-level nightly bundle (DB + secrets + Divar
// sessions + browser profiles + SSL). «همین حالا» only drops a request file
// for the host's systemd unit to pick up, so a run is polled rather than
// awaited; «تست همهٔ راه‌ها» reuses the same diagnose endpoint as the
// Telegram backup card, across every route at once.

import { HardDriveDownload, Loader2, Radar } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Lottie } from "@/components/ui/lottie";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ErrorNote, ListSkeleton, Section, ToneBadge } from "@/components/panel/kit";
import { IsoAlert } from "@/components/panel/motion3d";
import { Reveal } from "@/components/viz";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { faDate, faNum } from "@/lib/format";
import scanLottie from "@/lotties/scan.json";

// last_run/history come from app/services/dr_backup.py's ship(): the outcome
// sits under `sent`, not at the top level.
type DrRun = { stamp?: string; sent?: { ok?: boolean; at?: string; error?: string }; parts?: number };
type DrStatus = {
  last_run: DrRun | null;
  history: DrRun[];
  last_alert: { text: string; at: string } | null;
  requested: boolean;
  undelivered: string[];
  schedule_fa: string;
};
type DiagRow = { route: string; target: string; ok: boolean; http: number | null; error: string | null; ms: number };

const QKEY = ["users", "dr-status"] as const;
const POLL_MS = 4000;
const MAX_POLLS = 30;

/** The last runs, oldest to newest, one bar each: green when it shipped, red when
 *  it did not. Only what `history` carries; a run with no outcome is left out
 *  rather than guessed at. Bars grow from the baseline as the strip enters. */
function RunStrip({ history }: { history: DrRun[] }) {
  const runs = useMemo(() => {
    const dated = history.flatMap((r) => (r.sent?.at ? [{ at: Date.parse(r.sent.at), ok: !!r.sent.ok }] : []));
    return dated.filter((r) => Number.isFinite(r.at)).sort((a, b) => a.at - b.at).slice(-14);
  }, [history]);
  if (runs.length < 2) return null;
  const okCount = runs.filter((r) => r.ok).length;
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>سابقهٔ {faNum(runs.length)} اجرای اخیر</span>
        <span className="tabular">{faNum(okCount)} موفق{runs.length - okCount ? `، ${faNum(runs.length - okCount)} ناموفق` : ""}</span>
      </div>
      <motion.div
        role="img"
        aria-label={`سابقهٔ ${faNum(runs.length)} اجرای اخیر: ${faNum(okCount)} موفق`}
        className="flex h-7 items-end gap-1"
        initial="out"
        whileInView="in"
        viewport={{ once: true }}
        variants={{ out: {}, in: { transition: { staggerChildren: 0.04 } } }}
      >
        {runs.map((r) => (
          <motion.span
            key={r.at}
            title={`${faDate(new Date(r.at), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })} — ${r.ok ? "موفق" : "ناموفق"}`}
            className={`h-full flex-1 rounded-sm ${r.ok ? "bg-success" : "bg-destructive"}`}
            style={{ originY: 1 }}
            variants={{ out: { scaleY: 0 }, in: { scaleY: r.ok ? 1 : 0.6, transition: { duration: 0.5, ease: [0.22, 1, 0.36, 1] } } }}
          />
        ))}
      </motion.div>
    </div>
  );
}

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
          <ListSkeleton rows={2} />
        ) : q.isError ? (
          <ErrorNote error={q.error} illustration={<IsoAlert className="max-w-[80px]" />} />
        ) : (
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-muted-foreground">آخرین اجرا:</span>
              {q.data?.last_run?.sent?.at ? (
                <>
                  <span className="font-semibold">{faDate(new Date(q.data.last_run.sent.at), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span>
                  <ToneBadge tone={q.data.last_run.sent.ok ? "success" : "danger"}>{q.data.last_run.sent.ok ? "موفق" : "ناموفق"}</ToneBadge>
                  {!q.data.last_run.sent.ok && q.data.last_run.sent.error && (
                    <span className="text-xs text-muted-foreground">{q.data.last_run.sent.error}</span>
                  )}
                </>
              ) : <span>—</span>}
            </div>
            {busy && (
              <div role="status" className="flex items-center gap-2 text-xs text-muted-foreground">
                <Lottie animationData={scanLottie} className="size-12 shrink-0" /> در انتظار سرور — چند دقیقه طول می‌کشد
              </div>
            )}
            {q.data?.history && <RunStrip history={q.data.history} />}
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
