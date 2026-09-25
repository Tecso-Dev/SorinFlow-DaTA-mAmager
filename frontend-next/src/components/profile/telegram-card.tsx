"use client";

// اتصال به تلگرام (دستیار سورین) — a one-time code, sent to the bot in a
// private chat; polled every second client-side, re-checked with the server
// every 5th tick, until the link completes or the code expires.

import { Loader2, Send, Unlink } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Empty, Section, ToneBadge, useConfirm } from "@/components/panel/kit";
import { Reveal } from "@/components/viz";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { faDate, faNum } from "@/lib/format";

type Telegram = { linked: boolean; telegram_username?: string | null; linked_at?: string | null };
type LinkCode = { code: string; expires_in: number; command: string; deep_link: string | null };

const QKEY = ["profile", "telegram"] as const;

// Top-level so the linter's purity check (Date.now() must not run during
// render) sees a plain helper, not a closure the component body could call
// while rendering — these are only ever invoked from event handlers/effects.
const untilFor = (expiresIn: number) => Date.now() + expiresIn * 1000;
const secondsLeft = (until: number) => Math.max(0, Math.round((until - Date.now()) / 1000));

export function TelegramCard() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [link, setLink] = useState<LinkCode | null>(null);
  const [left, setLeft] = useState(0);
  const untilRef = useRef(0);
  const beforeRef = useRef<string | null>(null);
  const ticksRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [minting, setMinting] = useState(false);

  const q = useQuery({ queryKey: QKEY, queryFn: () => api<Telegram>("/users/me/telegram") });

  useEffect(() => () => { if (timerRef.current) clearInterval(timerRef.current); }, []);

  async function mint() {
    setMinting(true);
    try {
      const d = await api<LinkCode>("/users/me/telegram/link-code", { method: "POST" });
      beforeRef.current = q.data?.linked_at ?? null;
      untilRef.current = untilFor(d.expires_in);
      ticksRef.current = 0;
      setLink(d);
      setLeft(d.expires_in);
      if (timerRef.current) clearInterval(timerRef.current);
      timerRef.current = setInterval(tick, 1000);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "کد ساخته نشد");
    } finally {
      setMinting(false);
    }
  }

  async function tick() {
    const remain = secondsLeft(untilRef.current);
    setLeft(remain);
    if (!remain) {
      if (timerRef.current) clearInterval(timerRef.current);
      setLink(null);
      toast.error("کد منقضی شد؛ «دریافت کد اتصال» را دوباره بزنید");
      return;
    }
    ticksRef.current += 1;
    if (ticksRef.current % 5) return;
    const now = await qc.fetchQuery({ queryKey: QKEY, queryFn: () => api<Telegram>("/users/me/telegram") });
    if (now.linked && now.linked_at !== beforeRef.current) {
      if (timerRef.current) clearInterval(timerRef.current);
      setLink(null);
      toast.success("سورین حالا در تلگرام به شما جواب می‌دهد");
    }
  }

  async function unlink() {
    if (!(await confirm({
      title: "قطع اتصال تلگرام", danger: true, confirm: "قطع اتصال", icon: Unlink,
      description: "سورین دیگر در تلگرام به این حساب جواب نمی‌دهد، تا دوباره وصلش کنید.",
    }))) return;
    try {
      await api("/users/me/telegram", { method: "DELETE" });
      await qc.invalidateQueries({ queryKey: QKEY });
      toast.success("اتصال قطع شد");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "قطع اتصال ناموفق بود");
    }
  }

  const mm = Math.floor(left / 60);
  const ss = left % 60;

  return (
    <Reveal delay={0.1}>
      <Section title="اتصال به تلگرام (دستیار سورین)" action={q.data?.linked && <ToneBadge tone="success">وصل</ToneBadge>}>
        {q.isPending ? (
          <div className="h-16 animate-pulse rounded-lg bg-muted/40" />
        ) : q.isError ? (
          <Empty>بارگیری ناموفق بود</Empty>
        ) : q.data.linked ? (
          <div className="flex flex-col gap-3">
            <p className="text-sm text-muted-foreground">
              <span dir="ltr">{q.data.telegram_username ? `@${q.data.telegram_username}` : ""}</span>
              {q.data.linked_at && ` — از ${faDate(new Date(q.data.linked_at), { month: "short", day: "numeric", year: "numeric" })}`}
            </p>
            <Button variant="outline" size="sm" className="w-fit gap-1.5 text-destructive" onClick={unlink}>
              <Unlink className="size-4" /> قطع اتصال
            </Button>
          </div>
        ) : link ? (
          <div className="flex flex-col gap-3">
            <Input readOnly dir="ltr" value={link.code} className="text-center font-mono tracking-widest" aria-label="کد اتصال" />
            <p className="text-sm text-muted-foreground">
              در چت خصوصی با ربات دفتر بفرستید: <b dir="ltr">{link.command}</b>
            </p>
            {link.deep_link && (
              <Button asChild size="sm" className="w-fit gap-1.5">
                <a href={link.deep_link} target="_blank" rel="noopener">
                  <Send className="size-4" /> باز کردن ربات در تلگرام
                </a>
              </Button>
            )}
            <p className="text-xs text-muted-foreground tabular" dir="ltr">
              {faNum(mm)}:{faNum(ss).padStart(2, "۰")}
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <p className="text-sm text-muted-foreground">هنوز وصل نیست. کدی بگیرید و در تلگرام به ربات بفرستید.</p>
            <Button size="sm" className="w-fit gap-1.5" disabled={minting} onClick={mint}>
              {minting && <Loader2 className="size-4 animate-spin" />}
              دریافت کد اتصال
            </Button>
          </div>
        )}
      </Section>
    </Reveal>
  );
}
