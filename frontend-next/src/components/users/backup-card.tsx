"use client";

// بکاپ و نسخهٔ خارج از سرور — nightly DB snapshot to Telegram: bot token +
// chat id(s), the way out (manual proxy / dashboard proxy pool / Cloudflare
// relay — three mutually exclusive panes, only one sent per save), test the
// route, save, run now, and the morning digest (preview + send now).

import {
  Copy, Database, Loader2, Play, Radar, Search, Send, X,
} from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field, NativeSelect, RingDialog, Section, ToneBadge } from "@/components/panel/kit";
import { Reveal } from "@/components/viz";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { faDate, faNum } from "@/lib/format";

type Status = {
  configured: boolean; chat_ids: string[]; source: string; token_masked: string;
  proxy_mode: string; route_label: string; route_configured: boolean; proxy_masked: string;
  proxy_pool: string; relay: string; relay_key_set: boolean; proxy_source: string | null;
  schedule_fa: string; snapshots: { file: string; at: string; size: number }[]; snapshot_count: number;
  last_offsite: { at: string; ok: boolean } | null; digest_hour: number; digest_last_sent: string | null;
};

const QKEY = ["users", "backup-status"] as const;

function fmtSize(bytes: number) {
  if (bytes >= 1e9) return `${faNum(bytes / 1e9, { maximumFractionDigits: 1 })} گیگابایت`;
  return `${faNum(bytes / 1e6, { maximumFractionDigits: 1 })} مگابایت`;
}

export function BackupCard() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: QKEY, queryFn: () => api<Status>("/backup/status") });

  const [botToken, setBotToken] = useState("");
  const [chatId, setChatId] = useState("");
  const [mode, setMode] = useState<"manual" | "pool" | "relay">("manual");
  const [proxy, setProxy] = useState("");
  const [proxyPool, setProxyPool] = useState("*");
  const [relay, setRelay] = useState("");
  const [relayKey, setRelayKey] = useState("");
  const [hydrated, setHydrated] = useState(false);
  const [chats, setChats] = useState<{ id: string; title?: string }[] | null>(null);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [workerCode, setWorkerCode] = useState<string | null>(null);
  const [digestText, setDigestText] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  if (q.data && !hydrated) {
    setChatId(q.data.chat_ids.join(", "));
    setMode((["manual", "pool", "relay"].includes(q.data.proxy_mode) ? q.data.proxy_mode : "manual") as typeof mode);
    setProxyPool(q.data.proxy_pool || "*");
    setRelay(q.data.relay || "");
    setHydrated(true);
  }

  function routePayload() {
    return {
      proxy_mode: mode,
      proxy: mode === "manual" ? proxy || undefined : undefined,
      proxy_pool: mode === "pool" ? proxyPool || undefined : undefined,
      relay: mode === "relay" ? relay || undefined : undefined,
      relay_key: mode === "relay" && relayKey ? relayKey : undefined,
    };
  }

  async function find() {
    setBusy("find");
    setChats(null);
    try {
      const r = await api<{ chats: { id: string; title?: string }[]; hint_fa?: string }>("/backup/probe", {
        method: "POST", json: { bot_token: botToken || undefined, ...routePayload() },
      });
      setChats(r.chats);
      if (!r.chats.length && r.hint_fa) toast.error(r.hint_fa);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "پیدا نشد");
    } finally {
      setBusy(null);
    }
  }

  async function testRoute() {
    setBusy("test");
    setTestResult(null);
    try {
      const r = await api<{ bot?: { username?: string }; ms: number; results?: { proxy: string; ok: boolean }[] }>(
        "/backup/proxy-test", { method: "POST", json: { bot_token: botToken || undefined, ...routePayload() } },
      );
      setTestResult(`اتصال برقرار شد — ${faNum(r.ms)} میلی‌ثانیه`);
      toast.success("اتصال برقرار شد");
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "اتصال برقرار نشد";
      setTestResult(msg);
      toast.error(msg);
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    setBusy("save");
    try {
      await api("/backup/settings", {
        method: "PUT",
        json: { bot_token: botToken || undefined, chat_id: chatId, ...routePayload() },
      });
      setBotToken("");
      await qc.invalidateQueries({ queryKey: QKEY });
      toast.success("ذخیره شد");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "ذخیره نشد");
    } finally {
      setBusy(null);
    }
  }

  async function runNow() {
    setBusy("run");
    try {
      const r = await api<{ telegram_sent: boolean }>("/backup/run", { method: "POST" });
      await qc.invalidateQueries({ queryKey: QKEY });
      toast.success(r.telegram_sent ? "بکاپ گرفته و فرستاده شد" : "بکاپ گرفته شد (ارسال نشد)");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "بکاپ ناموفق بود");
    } finally {
      setBusy(null);
    }
  }

  async function openWorker() {
    setBusy("worker");
    try {
      setWorkerCode(await api<string>("/backup/relay-worker"));
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "کد Worker پیدا نشد");
    } finally {
      setBusy(null);
    }
  }

  async function openDigest() {
    setBusy("digest-preview");
    try {
      const r = await api<{ text: string }>("/backup/digest");
      setDigestText(r.text);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "بارگیری نشد");
    } finally {
      setBusy(null);
    }
  }

  async function sendDigest() {
    setBusy("digest-send");
    try {
      await api("/backup/digest/send", { method: "POST" });
      await qc.invalidateQueries({ queryKey: QKEY });
      toast.success("ارسال شد");
      setDigestText(null);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "ارسال نشد");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Reveal delay={0.1}>
      <Section
        title="بکاپ و نسخهٔ خارج از سرور"
        hint={q.data?.schedule_fa}
        action={q.data && <ToneBadge tone={q.data.configured ? "success" : "neutral"}>{q.data.configured ? "تنظیم‌شده" : "تنظیم‌نشده"}</ToneBadge>}
      >
        {q.isPending ? (
          <div className="h-32 animate-pulse rounded-lg bg-muted/40" />
        ) : (
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="توکن ربات تلگرام" htmlFor="bk-token" hint={q.data?.token_masked ? `ذخیره‌شده: ${q.data.token_masked}` : undefined}>
                <Input id="bk-token" dir="ltr" value={botToken} onChange={(e) => setBotToken(e.target.value)} placeholder="123456789:AA…" />
              </Field>
              <Field label="شناسهٔ چت (با ویرگول جدا کنید)" htmlFor="bk-chat">
                <div className="flex gap-1.5">
                  <Input id="bk-chat" dir="ltr" value={chatId} onChange={(e) => setChatId(e.target.value)} className="flex-1" />
                  <Button type="button" variant="outline" size="sm" className="shrink-0 gap-1" disabled={busy === "find"} onClick={find}>
                    {busy === "find" ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}
                    پیدا کن
                  </Button>
                </div>
              </Field>
            </div>

            {chats && (
              <div className="rounded-lg border p-2">
                {chats.length === 0 ? (
                  <p className="text-xs text-muted-foreground">چتی پیدا نشد.</p>
                ) : (
                  <ul className="flex flex-col gap-1">
                    {chats.map((c) => (
                      <li key={c.id}>
                        <button
                          type="button"
                          className="w-full rounded-md px-2 py-1 text-start text-sm hover:bg-accent"
                          onClick={() => { setChatId(c.id); setChats(null); }}
                        >
                          <span dir="ltr">{c.id}</span> {c.title && `— ${c.title}`}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            <Field label="راه رسیدن به تلگرام" htmlFor="bk-mode">
              <NativeSelect id="bk-mode" value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
                <option value="manual">پراکسی دستی</option>
                <option value="pool">استخر پراکسی‌های پنل</option>
                <option value="relay">رلهٔ Cloudflare Worker</option>
              </NativeSelect>
            </Field>

            {mode === "manual" && (
              <Field label="آدرس پراکسی" htmlFor="bk-proxy" hint={q.data?.proxy_masked ? `ذخیره‌شده: ${q.data.proxy_masked}` : "مثل socks5://user:pass@host:1080"}>
                <Input id="bk-proxy" dir="ltr" value={proxy} onChange={(e) => setProxy(e.target.value)} />
              </Field>
            )}
            {mode === "pool" && (
              <Field label="کدام پراکسی‌های پنل" htmlFor="bk-pool" hint="* یعنی همهٔ پراکسی‌های فعال">
                <Input id="bk-pool" dir="ltr" value={proxyPool} onChange={(e) => setProxyPool(e.target.value)} />
              </Field>
            )}
            {mode === "relay" && (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="آدرس رله" htmlFor="bk-relay" hint="مثل https://tg.example.com">
                  <Input id="bk-relay" dir="ltr" value={relay} onChange={(e) => setRelay(e.target.value)} />
                </Field>
                <Field label="کلید رله" htmlFor="bk-relay-key" hint={q.data?.relay_key_set ? "ذخیره‌شده — خالی یعنی بدون تغییر" : undefined}>
                  <Input id="bk-relay-key" dir="ltr" value={relayKey} onChange={(e) => setRelayKey(e.target.value)} />
                </Field>
                <Button type="button" variant="ghost" size="sm" className="w-fit gap-1.5 sm:col-span-2" disabled={busy === "worker"} onClick={openWorker}>
                  {busy === "worker" ? <Loader2 className="size-4 animate-spin" /> : <Copy className="size-4" />}
                  کد Worker
                </Button>
              </div>
            )}

            {testResult && <p className="text-xs text-muted-foreground">{testResult}</p>}

            <div className="flex flex-wrap gap-2 border-t pt-3">
              <Button size="sm" variant="outline" className="gap-1.5" disabled={!!busy} onClick={testRoute}>
                {busy === "test" ? <Loader2 className="size-4 animate-spin" /> : <Radar className="size-4" />}
                تست این راه
              </Button>
              <Button size="sm" variant="outline" disabled={!!busy} onClick={save}>
                {busy === "save" && <Loader2 className="size-4 animate-spin" />}
                ذخیره
              </Button>
              <Button size="sm" className="gap-1.5" disabled={!!busy} onClick={runNow}>
                {busy === "run" ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
                همین حالا بکاپ بگیر و بفرست
              </Button>
            </div>

            <div className="grid grid-cols-2 gap-3 border-t pt-3 text-sm sm:grid-cols-4">
              <div>
                <div className="text-xs text-muted-foreground">نسخه‌های محلی</div>
                <div className="font-semibold tabular">{faNum(q.data?.snapshot_count ?? 0)}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">آخرین ارسال خارجی</div>
                <div className="font-semibold">
                  {q.data?.last_offsite ? faDate(new Date(q.data.last_offsite.at), { month: "short", day: "numeric" }) : "—"}
                </div>
              </div>
              {q.data?.snapshots[0] && (
                <div className="col-span-2 sm:col-span-2">
                  <div className="text-xs text-muted-foreground">آخرین فایل</div>
                  <div className="truncate text-xs font-semibold tabular" dir="ltr">
                    {q.data.snapshots[0].file} ({fmtSize(q.data.snapshots[0].size)})
                  </div>
                </div>
              )}
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-3">
              <div>
                <div className="text-sm font-semibold">خلاصهٔ صبحگاهی</div>
                <div className="text-xs text-muted-foreground">
                  {q.data?.digest_last_sent ? `آخرین ارسال: ${faDate(new Date(q.data.digest_last_sent), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}` : "هنوز ارسال نشده"}
                </div>
              </div>
              <Button size="sm" variant="outline" className="gap-1.5" disabled={!!busy} onClick={openDigest}>
                {busy === "digest-preview" ? <Loader2 className="size-4 animate-spin" /> : <Database className="size-4" />}
                پیش‌نمایش و ارسال
              </Button>
            </div>
          </div>
        )}
      </Section>

      <RingDialog open={!!workerCode} onOpenChange={(o) => !o && setWorkerCode(null)} icon={Copy} title="کد Worker" wide>
        <div className="flex flex-col gap-2">
          <pre dir="ltr" className="max-h-80 overflow-auto rounded-lg bg-muted p-3 text-[11px] leading-5">{workerCode}</pre>
          <Button
            size="sm" variant="outline" className="w-fit gap-1.5"
            onClick={async () => { if (workerCode) { await navigator.clipboard.writeText(workerCode); toast.success("کپی شد"); } }}
          >
            <Copy className="size-4" /> کپی
          </Button>
        </div>
      </RingDialog>

      <RingDialog open={!!digestText} onOpenChange={(o) => !o && setDigestText(null)} icon={Send} title="خلاصهٔ صبحگاهی" wide>
        <div className="flex flex-col gap-3">
          <pre dir="rtl" className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-muted p-3 text-xs leading-6">{digestText}</pre>
          <div className="flex gap-2">
            <Button size="sm" className="gap-1.5" disabled={busy === "digest-send"} onClick={sendDigest}>
              {busy === "digest-send" ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
              ارسال الان
            </Button>
            <Button size="sm" variant="ghost" className="gap-1.5" onClick={() => setDigestText(null)}>
              <X className="size-4" /> بستن
            </Button>
          </div>
        </div>
      </RingDialog>
    </Reveal>
  );
}
