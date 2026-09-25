"use client";

// نمای کلی — the old monitoring section, unchanged in substance: health
// tiles, server/system tables, connectivity, Divar sessions, scraper + GCP,
// runtime (root/super_admin), client errors, the live chart and the log
// viewer. Mirrors frontend/js/app.js's monitoring block against
// app/api/routes/monitoring.py, gcp.py and stats.py.

import {
  AlertTriangle, Cable, CircleCheck, Cpu, Database, HardDrive, Loader2, Pause, Play, Radio,
  RotateCw, ShieldCheck, Terminal, Trash2, Wifi,
} from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { toast } from "@/components/toaster";
import { Reveal } from "@/components/viz";
import {
  Empty, ErrorNote, Field, ListSkeleton, NativeSelect, Section, ToneBadge, Toolbar, useConfirm,
} from "@/components/panel/kit";
import { api, ApiError } from "@/lib/api";
import { qs } from "@/lib/crm";
import { faDate, faNum, faPercent } from "@/lib/format";
import { can, type User } from "@/lib/session";

/* ───────────────────────── types (app/api/routes/monitoring.py, gcp.py, stats.py) ───────────────────────── */

type Overview = {
  uptime_seconds: number;
  system: { os: string; python: string; arch: string; hostname: string; distro?: string; distro_is_container?: boolean; host_uptime_seconds?: number };
  network: { server_ip: string; domain: string; reachability: Record<string, { up: boolean; status?: number; setup_ms?: number; error?: string; latency_ms?: number }> };
  scraper_running: number;
  services: { postgres: { up: boolean; latency_ms?: number }; redis: { up: boolean; latency_ms?: number; error?: string } };
  scraper: { jobs_by_status: Record<string, number>; stale_running: number; last_completed_at: string | null };
  resources: { cpu_count?: number; memory_used_bytes?: number; memory_limit_bytes?: number; swap_used_percent?: number; load_1m?: number; storage: { total_bytes?: number; free_bytes?: number; used_percent?: number } };
  totals: { properties: number; leads: number };
  metrics_enabled: boolean;
};
type LiveSnap = {
  ts: number; uptime_seconds: number; requests: number; errors: number; latency_sum: number; latency_count: number;
  cpu_usage_usec?: number; cpu_limit_cores?: number; cpu_count?: number;
  memory_used_bytes?: number; memory_limit_bytes?: number; swap_used_percent?: number; disk_used_percent?: number;
};
type Cookie = {
  id: number; phone_number: string; state: string; note: string; is_valid: boolean; expires_at: string | null;
  updated_at: string | null; age_hours: number | null; checked_age_minutes: number | null; verified: boolean; in_rotation: boolean;
};
type CookiesResp = { items: Cookie[]; total: number; usable: number; rotation_possible: boolean; rotate_every: number; check_every_minutes: number; stale_after_minutes: number };
type Runtime = { processes: { role?: string; host?: string; pid?: number; age_seconds: number; sandbox?: string }[]; loops: { name: string; stale: boolean; off?: boolean; last_beat?: number; started_at?: number }[]; queue_length: number; running: { job_id: string; worker: string }[] };
type ClientError = { at?: string; message?: string; url?: string; browser?: string; [k: string]: unknown };
type GcpStatus = { state: string; project_id: string | null; last_error?: string | null; last_export_at?: string | null };
type LogResp = { lines: string[]; note?: string; total_returned: number };

const SCRAPER_JOB_TONE: Record<string, "neutral" | "info" | "warning" | "success" | "danger"> = {
  pending: "neutral", running: "info", paused: "warning", completed: "success", failed: "danger", cancelled: "neutral",
};

function bytes(n?: number): string {
  if (n === undefined || n === null) return "—";
  if (n >= 1e9) return `${faNum(n / 1e9, { maximumFractionDigits: 1 })} گیگابایت`;
  if (n >= 1e6) return `${faNum(n / 1e6, { maximumFractionDigits: 1 })} مگابایت`;
  return `${faNum(n)} بایت`;
}
function duration(sec?: number): string {
  if (!sec && sec !== 0) return "—";
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  if (d > 0) return `${faNum(d)} روز و ${faNum(h)} ساعت`;
  if (h > 0) return `${faNum(h)} ساعت و ${faNum(m)} دقیقه`;
  return `${faNum(m)} دقیقه`;
}

/* ───────────────────────── health tiles ───────────────────────── */

function HealthTiles({ ov }: { ov: Overview }) {
  const diskPct = ov.resources.storage.used_percent ?? null;
  const diskTone = diskPct === null ? "" : diskPct > 90 ? "bg-destructive" : diskPct > 75 ? "bg-warning" : "bg-success";
  const items = [
    { key: "db", label: "پایگاه داده", icon: Database, ok: ov.services.postgres.up, sub: ov.services.postgres.up ? `${faNum(ov.services.postgres.latency_ms ?? 0, { maximumFractionDigits: 1 })}ms` : "قطع" },
    { key: "redis", label: "Redis", icon: Radio, ok: ov.services.redis.up, sub: ov.services.redis.up ? `${faNum(ov.services.redis.latency_ms ?? 0, { maximumFractionDigits: 1 })}ms` : ov.services.redis.error ?? "قطع" },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
      {items.map((it, i) => (
        <Reveal key={it.key} delay={i * 0.05}>
          <div className="flex h-full flex-col gap-2 rounded-2xl border bg-card p-4 shadow-sm dark:bg-linear-to-b dark:from-white/[0.04] dark:to-transparent dark:shadow-none">
            <div className="flex items-center gap-2">
              <div className={`grid size-9 place-items-center rounded-xl ${it.ok ? "bg-success/12 text-success" : "bg-destructive/12 text-destructive"}`}>
                <it.icon className="size-4.5" />
              </div>
              <div className="text-[13px] text-muted-foreground">{it.label}</div>
            </div>
            <div className="text-lg font-black tabular">{it.sub}</div>
          </div>
        </Reveal>
      ))}
      <Reveal delay={0.1}>
        <div className="flex h-full flex-col gap-2 rounded-2xl border bg-card p-4 shadow-sm dark:bg-linear-to-b dark:from-white/[0.04] dark:to-transparent dark:shadow-none">
          <div className="flex items-center gap-2">
            <div className="grid size-9 place-items-center rounded-xl bg-info/12 text-info"><HardDrive className="size-4.5" /></div>
            <div className="text-[13px] text-muted-foreground">فضای اشغال‌شدهٔ دیسک</div>
          </div>
          <div className="text-lg font-black tabular">{diskPct === null ? "—" : faPercent(diskPct)}</div>
          {diskPct !== null && (
            <div className="h-1.5 overflow-hidden rounded-full bg-muted">
              <div className={`h-full ${diskTone}`} style={{ width: `${Math.min(100, diskPct)}%` }} />
            </div>
          )}
        </div>
      </Reveal>
      <Reveal delay={0.15}>
        <div className="flex h-full flex-col gap-2 rounded-2xl border bg-card p-4 shadow-sm dark:bg-linear-to-b dark:from-white/[0.04] dark:to-transparent dark:shadow-none">
          <div className="flex items-center gap-2">
            <div className="grid size-9 place-items-center rounded-xl bg-primary/12 text-primary"><Cpu className="size-4.5" /></div>
            <div className="text-[13px] text-muted-foreground">مدت کارکرد + حافظه</div>
          </div>
          <div className="text-lg font-black tabular">{duration(ov.uptime_seconds)}</div>
          <div className="text-[11px] text-muted-foreground">{bytes(ov.resources.memory_used_bytes)} از {bytes(ov.resources.memory_limit_bytes)}</div>
        </div>
      </Reveal>
    </div>
  );
}

/* ───────────────────────── server & system tables ───────────────────────── */

function ServerTable({ ov }: { ov: Overview }) {
  const rows: [string, string][] = [
    ["سیستم‌عامل (کانتینر)", ov.system.distro ?? ov.system.os],
    ["معماری و پایتون", `${ov.system.arch} · Python ${ov.system.python}`],
    ["نام میزبان", ov.system.hostname],
    ["IP سرور", ov.network.server_ip || "—"],
    ["دامنه", ov.network.domain || "—"],
  ];
  return (
    <Section title="سرور و سیستم">
      <dl className="grid gap-2 text-sm sm:grid-cols-2">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-center justify-between gap-2 rounded-lg bg-muted/30 px-3 py-2">
            <dt className="text-muted-foreground">{k}</dt>
            <dd className="font-medium">{v}</dd>
          </div>
        ))}
      </dl>
    </Section>
  );
}

function UptimeTable({ ov }: { ov: Overview }) {
  const qc = useQueryClient();
  const [testing, setTesting] = useState(false);
  const divar = ov.network.reachability.divar;

  async function runTest() {
    setTesting(true);
    try {
      const res = await api<{ ok: boolean; total_ms: number }>("/monitoring/connectivity-test?target=divar", { method: "POST" });
      toast.success(res.ok ? "اتصال به دیوار برقرار است" : "اتصال به دیوار ناموفق بود", `${faNum(res.total_ms)}ms`);
      await qc.invalidateQueries({ queryKey: ["monitoring", "overview"] });
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "تست ناموفق بود");
    } finally {
      setTesting(false);
    }
  }

  return (
    <Section
      title="مدت کارکرد و اتصال"
      action={
        <Button variant="outline" size="sm" onClick={runTest} disabled={testing} className="gap-1.5">
          {testing ? <Loader2 className="size-3.5 animate-spin" /> : <Wifi className="size-3.5" />}
          تست واقعی
        </Button>
      }
    >
      <dl className="grid gap-2 text-sm sm:grid-cols-2">
        <div className="flex items-center justify-between gap-2 rounded-lg bg-muted/30 px-3 py-2">
          <dt className="text-muted-foreground">مدت کارکرد پردازه</dt>
          <dd className="font-medium tabular">{duration(ov.uptime_seconds)}</dd>
        </div>
        <div className="flex items-center justify-between gap-2 rounded-lg bg-muted/30 px-3 py-2">
          <dt className="text-muted-foreground">مدت کارکرد میزبان</dt>
          <dd className="font-medium tabular">{duration(ov.system.host_uptime_seconds)}</dd>
        </div>
        <div className="flex items-center justify-between gap-2 rounded-lg bg-muted/30 px-3 py-2">
          <dt className="text-muted-foreground">آخرین اسکرپ تمام‌شده</dt>
          <dd className="font-medium">{ov.scraper.last_completed_at ? faDate(new Date(ov.scraper.last_completed_at), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—"}</dd>
        </div>
        <div className="flex items-center justify-between gap-2 rounded-lg bg-muted/30 px-3 py-2">
          <dt className="text-muted-foreground">دسترسی به دیوار</dt>
          <dd><ToneBadge tone={divar?.up ? "success" : "danger"}>{divar?.up ? "برقرار" : "قطع"}</ToneBadge></dd>
        </div>
      </dl>
    </Section>
  );
}

/* ───────────────────────── Divar session health ───────────────────────── */

function CookiesTable() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["monitoring", "cookies"], queryFn: () => api<CookiesResp>("/monitoring/cookies"), refetchInterval: 30_000 });
  const [checking, setChecking] = useState<string | null>(null);

  async function check(phone: string) {
    setChecking(phone);
    try {
      const res = await api<{ alive: boolean | null }>(`/monitoring/cookies/check${qs({ phone })}`, { method: "POST" });
      toast[res.alive === true ? "success" : res.alive === false ? "error" : "info"](
        res.alive === true ? `${phone}: فعال` : res.alive === false ? `${phone}: باطل` : `${phone}: نامشخص`,
      );
      await qc.invalidateQueries({ queryKey: ["monitoring", "cookies"] });
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "بررسی نشد");
    } finally {
      setChecking(null);
    }
  }

  return (
    <Section title="وضعیت نشست‌های دیوار" hint={q.data ? `${faNum(q.data.usable)} از ${faNum(q.data.total)} قابل استفاده` : undefined}>
      {q.isPending ? (
        <ListSkeleton rows={3} />
      ) : q.isError ? (
        <ErrorNote error={q.error} />
      ) : q.data.items.length === 0 ? (
        <Empty icon={ShieldCheck}>هیچ نشست دیواری ثبت نشده است.</Empty>
      ) : (
        <div className="overflow-x-auto">
          <Table aria-label="نشست‌های دیوار">
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>شماره</TableHead>
                <TableHead>وضعیت</TableHead>
                <TableHead>آخرین بررسی</TableHead>
                <TableHead className="text-center">عملیات</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {q.data.items.map((c) => (
                <TableRow key={c.id}>
                  <TableCell dir="ltr" className="text-end tabular">{c.phone_number}</TableCell>
                  <TableCell>
                    <ToneBadge tone={c.state === "active" ? "success" : c.state === "expiring" ? "warning" : "danger"}>{c.note}</ToneBadge>
                    {!c.verified && <span className="ms-1.5 text-[11px] text-muted-foreground">(تأییدنشده)</span>}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {c.checked_age_minutes === null ? "هرگز" : `${faNum(Math.round(c.checked_age_minutes))} دقیقه پیش`}
                  </TableCell>
                  <TableCell>
                    <div className="flex justify-center">
                      <Button variant="outline" size="sm" className="gap-1.5" onClick={() => check(c.phone_number)} disabled={checking === c.phone_number}>
                        {checking === c.phone_number ? <Loader2 className="size-3.5 animate-spin" /> : <RotateCw className="size-3.5" />}
                        بررسی واقعی
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </Section>
  );
}

/* ───────────────────────── scraper + GCP ───────────────────────── */

function ScraperCard({ ov }: { ov: Overview }) {
  return (
    <Section title="وضعیت تسک‌های اسکرپر" hint={ov.scraper.stale_running > 0 ? `${faNum(ov.scraper.stale_running)} تسک بیش از ۶ ساعت در حال اجراست` : undefined}>
      <div className="flex flex-wrap gap-2">
        {Object.entries(ov.scraper.jobs_by_status).map(([status, count]) => (
          <ToneBadge key={status} tone={SCRAPER_JOB_TONE[status] ?? "neutral"}>{status}: {faNum(count)}</ToneBadge>
        ))}
        {Object.keys(ov.scraper.jobs_by_status).length === 0 && <span className="text-sm text-muted-foreground">تسکی ثبت نشده است.</span>}
      </div>
      {ov.scraper.stale_running > 0 && (
        <div className="mt-3 flex items-center gap-1.5 rounded-lg bg-warning/12 px-3 py-2 text-xs text-warning">
          <AlertTriangle className="size-3.5 shrink-0" /> تسک‌هایی بیش از ۶ ساعت در حال اجرا مانده‌اند.
        </div>
      )}
    </Section>
  );
}

function GcpCard() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["gcp", "status"], queryFn: () => api<GcpStatus>("/gcp/status") });
  const [testing, setTesting] = useState(false);
  const STATE_TONE: Record<string, "neutral" | "warning" | "danger" | "success"> = {
    disabled: "neutral", unconfigured: "warning", unreachable: "danger", connected: "success", starting: "warning",
  };

  async function test() {
    setTesting(true);
    try {
      const res = await api<{ ok: boolean; detail: string }>("/gcp/test", { method: "POST" });
      toast[res.ok ? "success" : "error"](res.ok ? "اتصال برقرار شد" : "اتصال ناموفق بود", res.detail);
      await qc.invalidateQueries({ queryKey: ["gcp", "status"] });
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "تست نشد");
    } finally {
      setTesting(false);
    }
  }

  return (
    <Section
      title="Google Cloud"
      action={
        <Button variant="outline" size="sm" onClick={test} disabled={testing} className="gap-1.5">
          {testing ? <Loader2 className="size-3.5 animate-spin" /> : <Cable className="size-3.5" />}
          تست
        </Button>
      }
    >
      {q.isPending ? (
        <ListSkeleton rows={2} />
      ) : q.isError ? (
        <ErrorNote error={q.error} />
      ) : (
        <div className="flex flex-col gap-2 text-sm">
          <ToneBadge tone={STATE_TONE[q.data.state] ?? "neutral"} className="w-fit">{q.data.state}</ToneBadge>
          {q.data.project_id && <div className="text-muted-foreground">پروژه: {q.data.project_id}</div>}
          {q.data.last_error && <div className="text-destructive">{q.data.last_error}</div>}
        </div>
      )}
    </Section>
  );
}

/* ───────────────────────── runtime (root/super_admin) ───────────────────────── */

function RuntimeCard() {
  const q = useQuery({ queryKey: ["monitoring", "runtime"], queryFn: () => api<Runtime>("/monitoring/runtime"), refetchInterval: 30_000 });
  return (
    <Section title="پردازه‌ها و کارهای پس‌زمینه" hint={q.data ? `صف: ${faNum(q.data.queue_length)} · در حال اجرا: ${faNum(q.data.running.length)}` : undefined}>
      {q.isPending ? (
        <ListSkeleton rows={3} />
      ) : q.isError ? (
        <ErrorNote error={q.error} />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <div>
            <div className="mb-2 text-xs font-semibold text-muted-foreground">پردازه‌ها</div>
            <ul className="flex flex-col gap-1.5 text-sm">
              {q.data.processes.map((p, i) => (
                <li key={i} className="flex items-center justify-between rounded-lg bg-muted/30 px-3 py-1.5">
                  <span>{p.role ?? "?"} · {p.host ?? "?"}</span>
                  <span className="tabular text-[11px] text-muted-foreground">{faNum(Math.round(p.age_seconds))}s پیش</span>
                </li>
              ))}
              {q.data.processes.length === 0 && <li className="text-muted-foreground">پردازه‌ای گزارش نشده است.</li>}
            </ul>
          </div>
          <div>
            <div className="mb-2 text-xs font-semibold text-muted-foreground">حلقه‌های پس‌زمینه</div>
            <ul className="flex flex-col gap-1.5 text-sm">
              {q.data.loops.map((l) => (
                <li key={l.name} className="flex items-center justify-between rounded-lg bg-muted/30 px-3 py-1.5">
                  <span>{l.name}</span>
                  <ToneBadge tone={l.off ? "neutral" : l.stale ? "danger" : "success"}>{l.off ? "خاموش" : l.stale ? "متوقف‌مانده" : "سالم"}</ToneBadge>
                </li>
              ))}
              {q.data.loops.length === 0 && <li className="text-muted-foreground">حلقه‌ای گزارش نشده است.</li>}
            </ul>
          </div>
        </div>
      )}
    </Section>
  );
}

/* ───────────────────────── client errors ───────────────────────── */

function ClientErrorsCard() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const q = useQuery({ queryKey: ["monitoring", "client-errors"], queryFn: () => api<{ items: ClientError[] }>("/monitoring/client-errors?limit=60") });

  async function clear() {
    if (!(await confirm({ title: "پاک‌سازی خطاهای مرورگر", description: "همهٔ خطاهای ثبت‌شده حذف شوند؟", confirm: "پاک‌سازی", danger: true, icon: Trash2 }))) return;
    try {
      await api("/monitoring/client-errors", { method: "DELETE" });
      await qc.invalidateQueries({ queryKey: ["monitoring", "client-errors"] });
      toast.success("پاک شد");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "پاک نشد");
    }
  }

  return (
    <Section
      title="خطاهای مرورگر کاربران"
      action={
        <Button variant="ghost" size="sm" className="gap-1.5 text-destructive" onClick={clear}>
          <Trash2 className="size-3.5" /> پاک‌سازی
        </Button>
      }
    >
      {q.isPending ? (
        <ListSkeleton rows={3} />
      ) : q.isError ? (
        <ErrorNote error={q.error} />
      ) : q.data.items.length === 0 ? (
        <Empty icon={CircleCheck}>خطایی ثبت نشده است.</Empty>
      ) : (
        <ul tabIndex={0} role="region" aria-label="خطاهای مرورگر کاربران" className="flex max-h-72 flex-col gap-1.5 overflow-y-auto text-xs">
          {q.data.items.map((e, i) => (
            <li key={i} className="rounded-lg bg-muted/30 px-3 py-2">
              <div className="font-medium">{String(e.message ?? "—")}</div>
              <div className="mt-0.5 truncate text-muted-foreground">{String(e.url ?? "")} · {String(e.browser ?? "")}</div>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

/* ───────────────────────── live chart ───────────────────────── */

function LiveCard() {
  const [paused, setPaused] = useState(false);
  const [samples, setSamples] = useState<LiveSnap[]>([]);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    async function tick() {
      try {
        const s = await api<LiveSnap>("/monitoring/live");
        setSamples((prev) => [...prev.slice(-29), s]);
      } catch {
        // a missed tick is not worth a toast — the next one tries again
      }
    }
    tick();
    if (!paused) timer.current = setInterval(tick, 5000);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [paused]);

  const last = samples[samples.length - 1];
  const prev = samples[samples.length - 2];
  const dt = last && prev ? Math.max(0.001, last.ts - prev.ts) : null;
  const reqRate = dt && last && prev ? (last.requests - prev.requests) / dt : null;
  const errRate = last && prev && last.requests > prev.requests
    ? ((last.errors - prev.errors) / (last.requests - prev.requests)) * 100 : 0;
  const cpuPct = dt && last?.cpu_usage_usec !== undefined && prev?.cpu_usage_usec !== undefined
    ? ((last.cpu_usage_usec - prev.cpu_usage_usec) / 1e6 / dt / (last.cpu_limit_cores || last.cpu_count || 1)) * 100
    : null;
  const ramPct = last?.memory_used_bytes && last?.memory_limit_bytes ? (last.memory_used_bytes / last.memory_limit_bytes) * 100 : null;

  const bar = (label: string, pct: number | null) => (
    <div>
      <div className="mb-1 flex items-center justify-between text-[11px] text-muted-foreground">
        <span>{label}</span><span className="tabular">{pct === null ? "—" : faPercent(pct)}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div className={`h-full ${pct !== null && pct > 90 ? "bg-destructive" : pct !== null && pct > 75 ? "bg-warning" : "bg-primary"}`} style={{ width: `${Math.min(100, pct ?? 0)}%` }} />
      </div>
    </div>
  );

  return (
    <Section
      title="وضعیت زندهٔ سرور"
      hint="هر ۵ ثانیه"
      action={
        <Button variant="outline" size="sm" className="gap-1.5" onClick={() => setPaused((p) => !p)}>
          {paused ? <Play className="size-3.5" /> : <Pause className="size-3.5" />}
          {paused ? "ادامه" : "توقف"}
        </Button>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="grid gap-2">
          <div className="text-2xl font-black tabular">{reqRate === null ? "—" : faNum(reqRate, { maximumFractionDigits: 2 })} <span className="text-xs font-normal text-muted-foreground">درخواست/ثانیه</span></div>
          <div className="text-sm text-muted-foreground">نرخ خطا: {faNum(errRate, { maximumFractionDigits: 1 })}٪</div>
        </div>
        <div className="grid gap-3">
          {bar("CPU", cpuPct)}
          {bar("RAM", ramPct)}
          {bar("Swap", last?.swap_used_percent ?? null)}
        </div>
      </div>
      {samples.length > 1 && (
        <div className="mt-4 flex h-16 items-end gap-0.5" aria-hidden>
          {samples.slice(1).map((s, i) => {
            const p = samples[i];
            const d = Math.max(0.001, s.ts - p.ts);
            const r = Math.max(0, (s.requests - p.requests) / d);
            const max = Math.max(1, ...samples.slice(1).map((s2, j) => {
              const p2 = samples[j];
              return Math.max(0, (s2.requests - p2.requests) / Math.max(0.001, s2.ts - p2.ts));
            }));
            return <div key={s.ts} className="flex-1 rounded-t bg-primary/60" style={{ height: `${Math.max(2, (r / max) * 100)}%` }} />;
          })}
        </div>
      )}
    </Section>
  );
}

/* ───────────────────────── log viewer ───────────────────────── */

function LogViewerCard() {
  const [log, setLog] = useState<"scraper.log" | "api.log" | "scheduler.log">("api.log");
  const [level, setLevel] = useState("");
  const [grep, setGrep] = useState("");
  const q = useQuery({
    queryKey: ["stats", "logs", log, level, grep],
    queryFn: () => api<LogResp>(`/stats/logs${qs({ log, level, grep, lines: 500 })}`),
  });
  return (
    <Section title="لاگ سامانه">
      <Toolbar className="mb-3">
        <Field label="فایل" htmlFor="mon-log-file" className="w-40">
          <NativeSelect id="mon-log-file" value={log} onChange={(e) => setLog(e.target.value as typeof log)}>
            <option value="api.log">api.log</option>
            <option value="scraper.log">scraper.log</option>
            <option value="scheduler.log">scheduler.log</option>
          </NativeSelect>
        </Field>
        <Field label="سطح" htmlFor="mon-log-level" className="w-32">
          <NativeSelect id="mon-log-level" value={level} onChange={(e) => setLevel(e.target.value)}>
            <option value="">همه</option>
            <option value="ERROR">ERROR</option>
            <option value="WARNING">WARNING</option>
            <option value="INFO">INFO</option>
          </NativeSelect>
        </Field>
        <Field label="جستجو" htmlFor="mon-log-grep" className="w-52">
          <Input id="mon-log-grep" dir="ltr" value={grep} onChange={(e) => setGrep(e.target.value)} placeholder="grep" />
        </Field>
      </Toolbar>
      {q.isPending ? (
        <ListSkeleton rows={4} />
      ) : q.isError ? (
        <ErrorNote error={q.error} />
      ) : q.data.lines.length === 0 ? (
        <Empty icon={Terminal}>{q.data.note ?? "خطی یافت نشد."}</Empty>
      ) : (
        <pre dir="ltr" tabIndex={0} role="region" aria-label="لاگ سامانه" className="max-h-96 overflow-auto rounded-xl bg-muted/40 p-3 text-start text-[11px] leading-5">
          {q.data.lines.join("\n")}
        </pre>
      )}
    </Section>
  );
}

/* ───────────────────────── page ───────────────────────── */

export function OverviewTab({ user }: { user?: User }) {
  const q = useQuery({ queryKey: ["monitoring", "overview"], queryFn: () => api<Overview>("/monitoring/overview"), refetchInterval: 30_000 });
  const isBoss = can(user, { roles: ["root", "super_admin"] });

  if (q.isPending) return <ListSkeleton rows={8} />;
  if (q.isError) return <ErrorNote error={q.error} />;
  const ov = q.data;

  return (
    <div className="flex flex-col gap-5">
      <HealthTiles ov={ov} />
      <div className="grid gap-5 lg:grid-cols-2">
        <Reveal><ServerTable ov={ov} /></Reveal>
        <Reveal delay={0.05}><UptimeTable ov={ov} /></Reveal>
      </div>
      <Reveal><CookiesTable /></Reveal>
      <div className="grid gap-5 lg:grid-cols-2">
        <Reveal><ScraperCard ov={ov} /></Reveal>
        <Reveal delay={0.05}><GcpCard /></Reveal>
      </div>
      {isBoss && <Reveal><RuntimeCard /></Reveal>}
      <Reveal><ClientErrorsCard /></Reveal>
      <Reveal><LiveCard /></Reveal>
      <Reveal><LogViewerCard /></Reveal>
    </div>
  );
}
