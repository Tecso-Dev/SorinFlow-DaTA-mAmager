"use client";

// The new monitoring contract (docs/MONITORING.md §4): سرور، کوبرنتیز،
// سرویس‌ها، CI/CD و هشدارها. root/super_admin only. Not every endpoint
// exists on every base yet — a 404 renders a graceful «هنوز در دسترس
// نیست» state instead of an error, per this stream's task.

import { Bell, Clock, GitBranch } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { motion } from "motion/react";
import { useState } from "react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Lottie } from "@/components/ui/lottie";
import { toast } from "@/components/toaster";
import { Empty, ErrorNote, ListSkeleton, Section, ToneBadge } from "@/components/panel/kit";
import { IsoAlert } from "@/components/panel/motion3d";
import { Reveal } from "@/components/viz";
import { api, ApiError } from "@/lib/api";
import { faDate, faNum, faPercent } from "@/lib/format";
import emptyLottie from "@/lotties/empty.json";
import successLottie from "@/lotties/success.json";

const errorArt = <IsoAlert />;
const emptyArt = <Lottie animationData={emptyLottie} className="max-w-[100px]" />;

/** A row plays its entrance once, when it first mounts; a refetch keeps its key and leaves it alone. */
const rowIn = (i: number) => ({
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.4, delay: Math.min(i, 10) * 0.035, ease: [0.22, 1, 0.36, 1] as const },
});

/** A share of something as a bar that fills from the start edge (the right, in this RTL panel) when it scrolls into view. */
function Meter({ label, value, pct, tone = "primary" }: { label: string; value: string; pct: number; tone?: "primary" | "success" | "warning" | "danger" }) {
  const fill = { primary: "bg-primary", success: "bg-success", warning: "bg-warning", danger: "bg-destructive" }[tone];
  const w = Math.min(100, Math.max(0, pct));
  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
        <span>{label}</span>
        <span className="tabular">{value}</span>
      </div>
      {/* the track is what is watched for entering the screen, never the fill:
          a scaleX(0) box has no area, and an observer given a zero-area target
          is not reliably told it is visible, so the fill would stay at zero */}
      <motion.div className="h-1.5 overflow-hidden rounded-full bg-muted" initial="out" whileInView="in" viewport={{ once: true }}>
        <motion.div
          className={cn("h-full rounded-full", fill)}
          style={{ width: `${w}%`, originX: 1 }}
          variants={{ out: { scaleX: 0 }, in: { scaleX: 1, transition: { duration: 0.9, ease: [0.22, 1, 0.36, 1] } } }}
        />
      </motion.div>
    </div>
  );
}

/** Uptime and share bars go amber and red as they get worse. */
const uptimeTone = (pct: number) => (pct >= 99 ? "success" : pct >= 95 ? "warning" : "danger") as "success" | "warning" | "danger";

/* ───────────────────────── shared: unavailable-aware query ───────────────────────── */

function useMonitoringQuery<T>(key: string[], path: string) {
  return useQuery<T, ApiError>({
    queryKey: key,
    queryFn: () => api<T>(path),
    retry: (count, err) => err.status !== 404 && count < 2,
  });
}

function NotYetAvailable({ endpoint }: { endpoint: string }) {
  return (
    <Empty illustration={emptyArt}>
      <div>هنوز در دسترس نیست.</div>
      <div dir="ltr" className="mt-1 text-[11px] text-muted-foreground">{endpoint}</div>
    </Empty>
  );
}

function Body<T>({ q, endpoint, children }: { q: ReturnType<typeof useMonitoringQuery<T>>; endpoint: string; children: (data: T) => React.ReactNode }) {
  if (q.isPending) return <ListSkeleton rows={4} />;
  if (q.isError) {
    if (q.error.status === 404) return <NotYetAvailable endpoint={endpoint} />;
    return <ErrorNote error={q.error} illustration={errorArt} />;
  }
  return <>{children(q.data)}</>;
}

/* ───────────────────────── سرور / کوبرنتیز — GET /monitoring/system ───────────────────────── */

type SystemResp = { at: string | null; age_s: number | null; stale: boolean; error: string | null; host: Record<string, unknown> | null; k8s: Record<string, unknown> | null };

function KeyValueList({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data);
  if (entries.length === 0) return <span className="text-sm text-muted-foreground">داده‌ای نیست.</span>;
  return (
    <dl className="grid gap-2 text-sm sm:grid-cols-2">
      {entries.map(([k, v], i) => (
        <motion.div key={k} {...rowIn(i)} className="flex items-center justify-between gap-2 rounded-lg bg-muted/30 px-3 py-2">
          <dt dir="ltr" className="text-muted-foreground">{k}</dt>
          <dd className="max-w-[60%] truncate font-medium">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
        </motion.div>
      ))}
    </dl>
  );
}

export function ServerTab() {
  const q = useMonitoringQuery<SystemResp>(["monitoring", "system"], "/monitoring/system");
  return (
    <Section
      title="سرور"
      hint={q.data ? `${q.data.stale ? "کهنه" : "به‌روز"} · ${q.data.at ? faDate(new Date(q.data.at), { hour: "2-digit", minute: "2-digit" }) : "—"}` : undefined}
      action={q.data && <ToneBadge tone={q.data.stale ? "warning" : "success"}>{q.data.stale ? "کهنه" : "زنده"}</ToneBadge>}
    >
      <Body q={q} endpoint="GET /api/monitoring/system">
        {(d) => (d.error ? <Empty illustration={errorArt}>{d.error}</Empty> : <KeyValueList data={d.host ?? {}} />)}
      </Body>
    </Section>
  );
}

export function K8sTab() {
  const q = useMonitoringQuery<SystemResp>(["monitoring", "system"], "/monitoring/system");
  return (
    <Section title="کوبرنتیز">
      <Body q={q} endpoint="GET /api/monitoring/system">
        {(d) => (d.k8s ? <KeyValueList data={d.k8s} /> : <Empty illustration={emptyArt}>اطلاعات کوبرنتیز در دسترس نیست (احتمالاً محیط لوکال).</Empty>)}
      </Body>
    </Section>
  );
}

/* ───────────────────────── سرویس‌ها — GET /monitoring/services ───────────────────────── */

type ServicesResp = {
  queue: { pending: number; running: number; claims: number; oldest_pending_s: number | null };
  redis: { used_mb: number; max_mb: number; evicted_keys: number; clients: number; uptime_s: number };
  postgres: { db_mb: number; connections: { active: number; idle: number; total: number }; longest_query_s: number | null };
  backups: { snapshot: { at: string | null; offsite_ok: boolean }; dr: { at: string | null; ok: boolean; alert: boolean } };
};

export function ServicesTab() {
  const q = useMonitoringQuery<ServicesResp>(["monitoring", "services"], "/monitoring/services");
  return (
    <Section title="سرویس‌ها">
      <Body q={q} endpoint="GET /api/monitoring/services">
        {(d) => (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Reveal className="rounded-xl border bg-muted/20 p-3">
              <div className="mb-1 text-xs text-muted-foreground">صف اسکرپ</div>
              <div className="tabular font-bold">{faNum(d.queue.pending)} در صف · {faNum(d.queue.running)} در حال اجرا</div>
            </Reveal>
            <Reveal delay={0.05} className="rounded-xl border bg-muted/20 p-3">
              <div className="mb-1 text-xs text-muted-foreground">Redis</div>
              <div className="tabular font-bold">{faNum(d.redis.used_mb, { maximumFractionDigits: 0 })} از {faNum(d.redis.max_mb, { maximumFractionDigits: 0 })} مگابایت</div>
              {d.redis.max_mb > 0 && (
                <div className="mt-2">
                  <Meter label="حافظهٔ پر" value={faPercent((d.redis.used_mb / d.redis.max_mb) * 100, 0)} pct={(d.redis.used_mb / d.redis.max_mb) * 100}
                    tone={d.redis.used_mb / d.redis.max_mb > 0.9 ? "danger" : d.redis.used_mb / d.redis.max_mb > 0.75 ? "warning" : "primary"} />
                </div>
              )}
            </Reveal>
            <Reveal delay={0.1} className="rounded-xl border bg-muted/20 p-3">
              <div className="mb-1 text-xs text-muted-foreground">Postgres</div>
              <div className="tabular font-bold">{faNum(d.postgres.db_mb, { maximumFractionDigits: 0 })} مگابایت · {faNum(d.postgres.connections.total)} اتصال</div>
              {d.postgres.connections.total > 0 && (
                <div className="mt-2">
                  <Meter label="اتصال‌های فعال" value={`${faNum(d.postgres.connections.active)} از ${faNum(d.postgres.connections.total)}`}
                    pct={(d.postgres.connections.active / d.postgres.connections.total) * 100} />
                </div>
              )}
            </Reveal>
            <Reveal delay={0.15} className="rounded-xl border bg-muted/20 p-3">
              <div className="mb-1 text-xs text-muted-foreground">بکاپ DR</div>
              <ToneBadge tone={d.backups.dr.alert ? "danger" : d.backups.dr.ok ? "success" : "warning"}>
                {d.backups.dr.at ? faDate(new Date(d.backups.dr.at), { month: "short", day: "numeric" }) : "—"}
              </ToneBadge>
            </Reveal>
          </div>
        )}
      </Body>
    </Section>
  );
}

/* ───────────────────────── CI/CD — GET /monitoring/cicd ───────────────────────── */

type CicdResp = {
  repo: string; deployed_sha: string; main_sha: string; behind: boolean;
  workflows: { name: string; file: string; last: { status: string; conclusion: string | null; branch: string; started_at: string | null } | null }[];
  uptime: { pct_24h: number | null; pct_7d: number | null; last_down_at: string | null };
};

export function CicdTab() {
  const q = useMonitoringQuery<CicdResp>(["monitoring", "cicd"], "/monitoring/cicd");
  return (
    <Section title="CI/CD">
      <Body q={q} endpoint="GET /api/monitoring/cicd">
        {(d) => (
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <ToneBadge tone={d.behind ? "warning" : "success"}>{d.behind ? "عقب از main" : "هم‌سطح main"}</ToneBadge>
              <span dir="ltr" className="tabular text-muted-foreground">deployed {d.deployed_sha} · main {d.main_sha}</span>
            </div>
            <ul className="grid gap-2 sm:grid-cols-2">
              {d.workflows.map((w, i) => (
                <motion.li key={w.file} {...rowIn(i)} className="flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-sm">
                  <span dir="ltr">{w.file}</span>
                  <ToneBadge tone={w.last?.conclusion === "success" ? "success" : w.last?.conclusion === "failure" ? "danger" : "neutral"}>
                    {w.last?.conclusion ?? "—"}
                  </ToneBadge>
                </motion.li>
              ))}
            </ul>
            <div className="grid gap-3 sm:grid-cols-2">
              {d.uptime.pct_24h !== null && (
                <Meter label="آپتایم ۲۴ ساعت" value={faPercent(d.uptime.pct_24h)} pct={d.uptime.pct_24h} tone={uptimeTone(d.uptime.pct_24h)} />
              )}
              {d.uptime.pct_7d !== null && (
                <Meter label="آپتایم ۷ روز" value={faPercent(d.uptime.pct_7d)} pct={d.uptime.pct_7d} tone={uptimeTone(d.uptime.pct_7d)} />
              )}
            </div>
            {d.uptime.pct_24h === null && d.uptime.pct_7d === null && (
              <div className="text-sm text-muted-foreground">آپتایم: هنوز نمونه‌ای ثبت نشده.</div>
            )}
          </div>
        )}
      </Body>
    </Section>
  );
}

/* ───────────────────────── هشدارها — GET /monitoring/alerts ───────────────────────── */

type Alert = { key: string; level: "warning" | "critical"; title: string; detail: string; since: string };
type AlertsResp = { active: Alert[]; log: Alert[]; telegram: { configured: boolean } };

export function AlertsTab() {
  const q = useMonitoringQuery<AlertsResp>(["monitoring", "alerts"], "/monitoring/alerts");
  const [sending, setSending] = useState(false);

  async function sendTest() {
    setSending(true);
    try {
      await api("/monitoring/alerts/test", { method: "POST" });
      toast.success("پیام آزمایشی پایش ارسال شد");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "ارسال نشد");
    } finally {
      setSending(false);
    }
  }

  return (
    <Section
      title="هشدارها"
      action={
        <Button variant="outline" size="sm" className="gap-1.5" onClick={sendTest} disabled={sending || q.isError}>
          <Bell className="size-3.5" /> پیام آزمایشی
        </Button>
      }
    >
      <Body q={q} endpoint="GET /api/monitoring/alerts">
        {(d) => (
          <div className="flex flex-col gap-4">
            {d.active.length === 0 ? (
              <Empty illustration={<Lottie animationData={successLottie} className="max-w-[92px]" />}>هشدار فعالی نیست.</Empty>
            ) : (
              <ul className="flex flex-col gap-2">
                {d.active.map((a, i) => (
                  <motion.li key={a.key} {...rowIn(i)} className="flex items-start gap-2 rounded-lg border px-3 py-2 text-sm">
                    <ToneBadge tone={a.level === "critical" ? "danger" : "warning"}>{a.level === "critical" ? "بحرانی" : "هشدار"}</ToneBadge>
                    <div className="min-w-0 flex-1">
                      <div className="font-medium">{a.title}</div>
                      <div className="text-xs text-muted-foreground">{a.detail}</div>
                    </div>
                  </motion.li>
                ))}
              </ul>
            )}
            {d.log.length > 0 && (
              <div>
                <div className="mb-2 flex items-center gap-1.5 text-xs text-muted-foreground"><Clock className="size-3.5" /> تاریخچه</div>
                <ul tabIndex={0} aria-label="تاریخچهٔ هشدارها" className="flex max-h-64 flex-col gap-1.5 overflow-y-auto text-xs">
                  {d.log.map((a, i) => (
                    <motion.li key={i} {...rowIn(i)} className="rounded-lg bg-muted/30 px-3 py-1.5">
                      {a.title} — {faDate(new Date(a.since), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                    </motion.li>
                  ))}
                </ul>
              </div>
            )}
            <div className="text-[11px] text-muted-foreground">
              <GitBranch className="me-1 inline size-3" />
              تلگرام: {d.telegram.configured ? "تنظیم‌شده" : "تنظیم‌نشده"}
            </div>
          </div>
        )}
      </Body>
    </Section>
  );
}
