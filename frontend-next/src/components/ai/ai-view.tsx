"use client";

// هوش مصنوعی — connection, agents, call log, «سورین» assistant and settings.
// root/super_admin only. Mirrors frontend/js/app.js loadAi()/loadAiScreen()
// against app/api/routes/ai.py + ai_assistant.py.

import {
  Bot, CircleAlert, Gauge, Loader2, MessageCircle, Play, Plug, Save, Send, ShieldAlert, Sparkles, Wallet, Zap,
} from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/components/toaster";
import { Reveal, Tilt } from "@/components/viz";
import {
  Empty, ErrorNote, Field, ListSkeleton, NativeSelect, PageHeader, Section, ToneBadge, Toolbar,
} from "@/components/panel/kit";
import { api, ApiError } from "@/lib/api";
import { qs } from "@/lib/crm";
import { faDate, faNum } from "@/lib/format";
import { useSession, can } from "@/lib/session";

/* ───────────────────────── types (app/api/routes/ai.py, ai_assistant.py) ───────────────────────── */

type AgentUsage = { calls: number; cost_usd: number; cost_toman: number; failed: number };
type Agent = {
  key: string; name: string; job: string; kind: string; desc: string; where: string[]; status_url?: string;
  enabled: boolean; model: string; state: Record<string, unknown>; cap_usd: number; month: AgentUsage; today: AgentUsage;
};
type Overview = {
  configured: boolean; enabled: boolean; workspace: string;
  models: Record<string, string>; model_sources: Record<string, string>; cap_usd: number;
  agent_caps: Record<string, number>; notes: string;
  key_set: boolean; base_url_set: boolean; env_models: Record<string, string>;
  usage: { today: { calls: number; cost_usd: number; cost_toman: number; tokens: number }; month: { calls: number; cost_usd: number; cost_toman: number; tokens: number } };
  spent_today_usd: number; cap_reached: boolean;
  breaker: { state: "closed" | "open" | "half_open"; until: string | null };
  liara: { days: number; models: unknown[]; cost_toman: number; calls: number } | null;
  quota: { plan: unknown; daily: Record<string, unknown>; monthly: Record<string, unknown> } | null;
  agents: Agent[];
};
type AiUsageRow = { id: number; agent: string; job: string; model: string | null; cost_usd: number; cost_toman: number; ms: number; ok: boolean; error: string | null; created_at: string | null };
type AssistantStatus = { name: string; enabled: boolean; configured: boolean; telegram_configured: boolean; linked_users: string[]; questions_today: number; last: { question: string; answer: string | null; created_at: string | null } | null };
type AiChat = { id: number; chat_id: string; who: string; question: string; answer: string | null; tools: string[]; ms: number; ok: boolean; error: string | null; created_at: string | null };

const JOB_LABEL: Record<string, string> = { write: "نوشتن", read: "خواندن", vision: "تصویر", embed: "بردار" };

/* ───────────────────────── tiles ───────────────────────── */

function Tiles({ ov }: { ov?: Overview }) {
  const items = [
    {
      key: "conn", label: "اتصال", icon: Plug, value: null,
      text: ov ? (ov.configured && ov.enabled ? "متصل" : ov.configured ? "خاموش" : "تنظیم نشده") : "—",
      tint: ov?.configured && ov?.enabled ? "text-success bg-success/12" : "text-warning bg-warning/12",
    },
    {
      key: "today", label: "هزینهٔ امروز", icon: Wallet, value: null,
      text: ov ? `${faNum(ov.spent_today_usd, { maximumFractionDigits: 2 })}$ از ${faNum(ov.cap_usd, { maximumFractionDigits: 0 })}$` : "—",
      tint: ov?.cap_reached ? "text-destructive bg-destructive/12" : "text-primary bg-primary/12",
    },
    {
      key: "month", label: "این ماه", icon: Gauge, value: null,
      text: ov ? `${faNum(ov.usage.month.cost_usd, { maximumFractionDigits: 2 })}$ · ${faNum(ov.usage.month.calls)} فراخوانی` : "—",
      tint: "text-info bg-info/12",
    },
    {
      key: "liara", label: "توکن رایگان لیارا امروز", icon: Zap, value: null,
      text: ov?.quota?.daily ? `${faNum(Number(ov.quota.daily.used ?? 0))} از ${faNum(Number(ov.quota.daily.limit ?? 0))}` : "در دسترس نیست",
      tint: "text-chart-5 bg-chart-5/15",
    },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
      {items.map((it, i) => (
        <Reveal key={it.key} delay={i * 0.05}>
          <Tilt className="flex h-full flex-col gap-3 rounded-2xl border bg-card p-4 shadow-sm dark:bg-linear-to-b dark:from-white/[0.04] dark:to-transparent dark:shadow-none">
            <div className={`grid size-10 shrink-0 place-items-center rounded-xl ring-1 ring-inset ring-current/20 shadow-[0_0_20px_-6px_currentColor] ${it.tint}`}>
              <it.icon className="size-5" />
            </div>
            <div>
              <div className="text-[13px] text-muted-foreground">{it.label}</div>
              <div className="mt-1 text-[20px] leading-tight font-black tabular">{it.text}</div>
            </div>
          </Tilt>
        </Reveal>
      ))}
    </div>
  );
}

/* ───────────────────────── agent card ───────────────────────── */

function AgentCard({ agent, onErrorClick }: { agent: Agent; onErrorClick: (key: string) => void }) {
  const qc = useQueryClient();
  const [capOpen, setCapOpen] = useState(false);
  const [cap, setCap] = useState(String(agent.cap_usd));
  const [runBusy, setRunBusy] = useState(false);
  const canRun = ["reader", "embed", "vision"].includes(agent.key);
  const runPath = agent.key === "reader" ? "/ai/reader/run" : agent.key === "embed" ? "/ai/embed/run" : "/ai/photo/run";

  async function toggle(v: boolean) {
    try {
      await api(`/ai/agents/${agent.key}`, { method: "PUT", json: { enabled: v } });
      await qc.invalidateQueries({ queryKey: ["ai", "overview"] });
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "ذخیره نشد");
    }
  }

  async function saveCap() {
    const n = Number(cap);
    if (!Number.isFinite(n) || n < 0 || n > 100) {
      toast.error("سقف باید بین ۰ تا ۱۰۰ باشد");
      return;
    }
    try {
      await api(`/ai/agents/${agent.key}/cap`, { method: "PUT", json: { cap_usd: n } });
      await qc.invalidateQueries({ queryKey: ["ai", "overview"] });
      setCapOpen(false);
      toast.success("سقف ایجنت ذخیره شد");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "ذخیره نشد");
    }
  }

  async function runOnce() {
    setRunBusy(true);
    try {
      await api(runPath, { method: "POST" });
      await qc.invalidateQueries({ queryKey: ["ai", "overview"] });
      toast.success("یک دور اجرا شد");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "اجرا نشد");
    } finally {
      setRunBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-2xl border bg-card p-4 shadow-sm dark:bg-linear-to-b dark:from-white/[0.035] dark:to-white/[0.008]">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 font-bold">
            <Bot className="size-4 shrink-0 text-primary" />
            <span className="truncate">{agent.name}</span>
          </div>
          <p className="mt-1 line-clamp-2 text-[11px] text-muted-foreground">{agent.desc}</p>
        </div>
        <Switch checked={agent.enabled} onCheckedChange={toggle} aria-label={`فعال بودن ${agent.name}`} />
      </div>
      <div className="grid grid-cols-2 gap-2 text-[11px]">
        <div className="rounded-lg bg-muted/40 px-2 py-1.5">
          <div className="text-muted-foreground">امروز</div>
          <div className="tabular font-semibold">{faNum(agent.today.calls)} فراخوانی · {faNum(agent.today.cost_usd, { maximumFractionDigits: 3 })}$</div>
        </div>
        <div className="rounded-lg bg-muted/40 px-2 py-1.5">
          <div className="text-muted-foreground">این ماه</div>
          <div className="tabular font-semibold">{faNum(agent.month.calls)} فراخوانی · {faNum(agent.month.cost_usd, { maximumFractionDigits: 3 })}$</div>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <Button variant="outline" size="xs" onClick={() => setCapOpen((o) => !o)}>سقف: {faNum(agent.cap_usd, { maximumFractionDigits: 2 })}$</Button>
        {canRun && (
          <Button variant="outline" size="xs" className="gap-1" onClick={runOnce} disabled={runBusy}>
            {runBusy ? <Loader2 className="size-3 animate-spin" /> : <Play className="size-3" />}
            اجرای یک دور
          </Button>
        )}
        {(agent.today.failed > 0 || agent.month.failed > 0) && (
          <button
            type="button"
            onClick={() => onErrorClick(agent.key)}
            className="inline-flex items-center gap-1 rounded-full bg-destructive/12 px-2 py-0.5 text-[11px] font-semibold text-destructive"
          >
            <ShieldAlert className="size-3" /> {faNum(agent.today.failed)} خطا
          </button>
        )}
      </div>
      {capOpen && (
        <div className="flex items-center gap-2">
          <Input dir="ltr" inputMode="decimal" value={cap} onChange={(e) => setCap(e.target.value)} className="h-8 max-w-24" />
          <Button size="sm" onClick={saveCap}>ذخیره</Button>
        </div>
      )}
    </div>
  );
}

/* ───────────────────────── log ───────────────────────── */

function LogCard({ agentFilter, onAgentFilter }: { agentFilter: { agent: string; failedOnly: boolean }; onAgentFilter: (v: { agent: string; failedOnly: boolean }) => void }) {
  const q = useQuery({
    queryKey: ["ai", "log", agentFilter.agent, agentFilter.failedOnly],
    queryFn: () => api<{ items: AiUsageRow[]; agents: string[] }>(`/ai/log${qs({ agent: agentFilter.agent, failed_only: agentFilter.failedOnly, limit: 100 })}`),
  });
  return (
    <Section title="لاگ فراخوانی‌ها" hint={q.data ? `${faNum(q.data.items.length)} ردیف` : undefined}>
      <Toolbar className="mb-3">
        <Field label="ایجنت" htmlFor="ai-log-agent" className="w-40">
          <NativeSelect id="ai-log-agent" value={agentFilter.agent} onChange={(e) => onAgentFilter({ ...agentFilter, agent: e.target.value })}>
            <option value="">همه</option>
            {(q.data?.agents ?? []).map((a) => <option key={a} value={a}>{a}</option>)}
          </NativeSelect>
        </Field>
        <label className="flex items-center gap-1.5 self-end pb-1.5 text-sm">
          <input type="checkbox" className="accent-primary" checked={agentFilter.failedOnly} onChange={(e) => onAgentFilter({ ...agentFilter, failedOnly: e.target.checked })} />
          فقط خطاها
        </label>
      </Toolbar>
      {q.isPending ? (
        <ListSkeleton rows={5} />
      ) : q.isError ? (
        <ErrorNote error={q.error} />
      ) : q.data.items.length === 0 ? (
        <Empty icon={CircleAlert}>فراخوانی‌ای ثبت نشده است.</Empty>
      ) : (
        <ul tabIndex={0} aria-label="لاگ فراخوانی‌ها" className="flex max-h-96 flex-col gap-1.5 overflow-y-auto text-sm">
          {q.data.items.map((r) => (
            <li key={r.id} className="flex items-center gap-2 rounded-lg border px-3 py-2">
              <ToneBadge tone={r.ok ? "success" : "danger"}>{r.agent}</ToneBadge>
              <span className="min-w-0 flex-1 truncate text-muted-foreground">{r.ok ? (r.model ?? "—") : r.error}</span>
              <span className="shrink-0 tabular text-[11px] text-muted-foreground">
                {faNum(r.cost_usd, { maximumFractionDigits: 4 })}$ · {faNum(r.ms)}ms
              </span>
              <span className="shrink-0 text-[11px] text-muted-foreground">
                {r.created_at ? faDate(new Date(r.created_at), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

/* ───────────────────────── assistant Q&A ───────────────────────── */

function AssistantCard() {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["ai", "assistant", "status"], queryFn: () => api<AssistantStatus>("/ai/assistant/status") });
  const log = useQuery({ queryKey: ["ai", "assistant", "log"], queryFn: () => api<{ items: AiChat[] }>("/ai/assistant/log?limit=30") });
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);

  async function ask() {
    if (!question.trim()) return;
    setBusy(true);
    try {
      await api("/ai/assistant/ask", { json: { text: question.trim() } });
      setQuestion("");
      await qc.invalidateQueries({ queryKey: ["ai", "assistant", "log"] });
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "پاسخی دریافت نشد");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section
      title="سؤال‌های دستیار «سورین»"
      hint={status.data ? `${faNum(status.data.questions_today)} سؤال امروز · ${faNum(status.data.linked_users.length)} کاربر وصل‌شده` : undefined}
      action={status.data && <ToneBadge tone={status.data.enabled ? "success" : "neutral"}>{status.data.enabled ? "فعال" : "خاموش"}</ToneBadge>}
    >
      <div className="mb-3 flex gap-2">
        <Input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="از سورین بپرس…" onKeyDown={(e) => e.key === "Enter" && ask()} />
        <Button onClick={ask} disabled={busy} className="shrink-0 gap-1.5">
          {busy ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
          بپرس
        </Button>
      </div>
      {log.isPending ? (
        <ListSkeleton rows={3} />
      ) : log.isError ? (
        <ErrorNote error={log.error} />
      ) : log.data.items.length === 0 ? (
        <Empty icon={MessageCircle}>سؤالی ثبت نشده است.</Empty>
      ) : (
        <ul tabIndex={0} aria-label="سؤال‌های دستیار سورین" className="flex max-h-96 flex-col gap-3 overflow-y-auto text-sm">
          {log.data.items.map((c) => (
            <li key={c.id} className="rounded-xl border px-3 py-2.5">
              <div className="flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
                <span>{c.who || "—"}</span>
                <span>{c.created_at ? faDate(new Date(c.created_at), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—"}</span>
              </div>
              <div className="mt-1 font-medium">{c.question}</div>
              {c.ok ? (
                <div className="mt-1 whitespace-pre-wrap text-muted-foreground">{c.answer}</div>
              ) : (
                <div className="mt-1 text-destructive">{c.error}</div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

/* ───────────────────────── settings ───────────────────────── */

function SettingsCard({ ov }: { ov?: Overview }) {
  const qc = useQueryClient();
  const [fields, setFields] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [testBusy, setTestBusy] = useState(false);

  if (!ov) return <Section title="تنظیمات هوش مصنوعی"><ListSkeleton rows={4} /></Section>;

  const modelVal = (job: string) => fields[`model_${job}`] ?? ov.models[job] ?? "";
  const notes = fields.notes ?? ov.notes;
  const cap = fields.daily_cap_usd ?? String(ov.cap_usd);

  async function save() {
    setBusy(true);
    try {
      const body: Record<string, unknown> = {};
      for (const job of ["write", "read", "vision", "embed"]) {
        if (fields[`model_${job}`] !== undefined) body[`model_${job}`] = fields[`model_${job}`];
      }
      if (fields.notes !== undefined) body.notes = fields.notes;
      if (fields.daily_cap_usd !== undefined) body.daily_cap_usd = Number(fields.daily_cap_usd);
      await api("/ai/settings", { method: "PUT", json: body });
      await qc.invalidateQueries({ queryKey: ["ai", "overview"] });
      setFields({});
      toast.success("تنظیمات هوش مصنوعی ذخیره شد");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "ذخیره نشد");
    } finally {
      setBusy(false);
    }
  }

  async function toggleEnabled(v: boolean) {
    try {
      await api("/ai/settings", { method: "PUT", json: { enabled: v } });
      await qc.invalidateQueries({ queryKey: ["ai", "overview"] });
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "ذخیره نشد");
    }
  }

  async function test() {
    setTestBusy(true);
    try {
      const res = await api<{ model: string; ms: number }>("/ai/test", { method: "POST" });
      toast.success("اتصال برقرار است", `${res.model} در ${faNum(res.ms)}ms`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "اتصال ناموفق بود");
    } finally {
      setTestBusy(false);
    }
  }

  return (
    <Section
      title="تنظیمات هوش مصنوعی"
      action={
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {ov.breaker.state !== "closed" && (
            <ToneBadge tone="warning">
              مکث {ov.breaker.until ? `تا ${faDate(new Date(ov.breaker.until), { hour: "2-digit", minute: "2-digit" })}` : ""}
            </ToneBadge>
          )}
          <ToneBadge tone={ov.key_set && ov.base_url_set ? "success" : "warning"}>{ov.key_set && ov.base_url_set ? "کلید تنظیم شده" : "کلید تنظیم نشده"}</ToneBadge>
        </div>
      }
    >
      <div className="grid gap-4">
        <div className="grid gap-3 sm:grid-cols-2">
          {(["write", "read", "vision", "embed"] as const).map((job) => (
            <Field key={job} label={`مدل ${JOB_LABEL[job]}`} htmlFor={`ai-model-${job}`} hint={ov.env_models[job] ? `پیش‌فرض محیط: ${ov.env_models[job]}` : undefined}>
              <Input id={`ai-model-${job}`} dir="ltr" value={modelVal(job)} placeholder={ov.env_models[job] || "—"}
                onChange={(e) => setFields((f) => ({ ...f, [`model_${job}`]: e.target.value }))} />
            </Field>
          ))}
        </div>
        <Field label="سقف روزانه (دلار)" htmlFor="ai-cap" className="max-w-48">
          <Input id="ai-cap" dir="ltr" inputMode="decimal" value={cap} onChange={(e) => setFields((f) => ({ ...f, daily_cap_usd: e.target.value }))} />
        </Field>
        <Field label="یادداشت دفتر" htmlFor="ai-notes" hint={`${faNum((notes || "").length)}/۶۰۰`}>
          <Textarea id="ai-notes" rows={3} maxLength={600} value={notes} onChange={(e) => setFields((f) => ({ ...f, notes: e.target.value }))} />
        </Field>
        <div className="flex items-center justify-between rounded-xl border bg-muted/30 px-4 py-3">
          <div>
            <div className="text-sm font-semibold">هوش مصنوعی فعال است</div>
            <div className="text-xs text-muted-foreground">خاموش کردن، همهٔ ایجنت‌ها را متوقف می‌کند</div>
          </div>
          <Switch checked={ov.enabled} onCheckedChange={toggleEnabled} aria-label="فعال بودن هوش مصنوعی" />
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={save} disabled={busy} className="gap-1.5">
            {busy ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
            ذخیره
          </Button>
          <Button variant="outline" onClick={test} disabled={testBusy} className="gap-1.5">
            {testBusy ? <Loader2 className="size-4 animate-spin" /> : <Plug className="size-4" />}
            تست اتصال
          </Button>
        </div>
      </div>
    </Section>
  );
}

/* ───────────────────────── page ───────────────────────── */

export function AiView() {
  const user = useSession().data?.user;
  const allowed = can(user, { roles: ["root", "super_admin"] });
  const q = useQuery({ queryKey: ["ai", "overview"], queryFn: () => api<Overview>("/ai/overview"), enabled: allowed });
  const [agentFilter, setAgentFilter] = useState({ agent: "", failedOnly: false });

  if (!allowed) {
    return (
      <div className="flex flex-col gap-5">
        <PageHeader icon={Sparkles} title="هوش مصنوعی" />
        <Empty icon={ShieldAlert}>این بخش فقط برای مدیر ارشد و root در دسترس است.</Empty>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader icon={Sparkles} title="هوش مصنوعی" hint="ایجنت‌ها، دستیار «سورین» و تنظیمات مدل" />
      {q.isPending ? (
        <ListSkeleton rows={6} />
      ) : q.isError ? (
        <ErrorNote error={q.error} />
      ) : (
        <>
          <Tiles ov={q.data} />
          <Reveal>
            <Section title="ایجنت‌ها">
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {q.data.agents.map((a) => (
                  <AgentCard key={a.key} agent={a} onErrorClick={(key) => setAgentFilter({ agent: key, failedOnly: true })} />
                ))}
              </div>
            </Section>
          </Reveal>
          <Reveal><LogCard agentFilter={agentFilter} onAgentFilter={setAgentFilter} /></Reveal>
          <Reveal><AssistantCard /></Reveal>
          <Reveal><SettingsCard ov={q.data} /></Reveal>
        </>
      )}
    </div>
  );
}
