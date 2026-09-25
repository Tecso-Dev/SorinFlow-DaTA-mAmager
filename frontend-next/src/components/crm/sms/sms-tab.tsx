"use client";

// پیامک — send form with a live character/segment counter, and the log list.
// POST /crm/sms/send, GET /crm/sms/logs.

import { Loader2, MessageSquareText, RefreshCw, Send } from "lucide-react";
import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/components/toaster";
import { Reveal } from "@/components/viz";
import { Empty, ErrorNote, Field, ListSkeleton, NativeSelect, PageHeader, Section, ToneBadge } from "@/components/panel/kit";
import { api, ApiError } from "@/lib/api";
import { faDate, faNum, parseDigits } from "@/lib/format";

type SmsLog = {
  id: number;
  to_number: string;
  message: string;
  status: string;
  provider: string;
  response: string | null;
  sent_at: string | null;
};

const PROVIDER_LABEL: Record<string, string> = { kavenegar: "کاوه‌نگار", melipayamak: "ملی‌پیامک" };
const QKEY = ["crm", "sms-logs"] as const;

/**
 * Persian SMS segmentation (UCS-2, as the office's providers bill it): one
 * part holds 70 characters; once a message needs more than one part, each
 * part shrinks to 67 to make room for the concatenation header.
 */
function smsSegments(text: string) {
  const length = text.length;
  if (length === 0) return { length, parts: 0, perPart: 70 };
  if (length <= 70) return { length, parts: 1, perPart: 70 };
  return { length, parts: Math.ceil(length / 67), perPart: 67 };
}

function SendForm() {
  const qc = useQueryClient();
  const [to, setTo] = useState("");
  const [message, setMessage] = useState("");
  const [provider, setProvider] = useState("kavenegar");
  const [busy, setBusy] = useState(false);
  const seg = useMemo(() => smsSegments(message), [message]);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const toNumber = parseDigits(to.trim());
    if (!toNumber || !message.trim()) {
      toast.error("شماره و متن پیامک الزامی است");
      return;
    }
    setBusy(true);
    try {
      const result = await api<{ success: boolean; response?: string }>("/crm/sms/send", {
        json: { to_number: toNumber, message: message.trim(), provider },
      });
      if (result.success) {
        toast.success("پیامک ارسال شد");
        setMessage("");
        await qc.invalidateQueries({ queryKey: QKEY });
      } else {
        toast.error(result.response || "ارسال ناموفق بود");
      }
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "ارسال ناموفق بود");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title="ارسال پیامک">
      <form onSubmit={send} className="grid gap-3 sm:grid-cols-2">
        <Field label="شمارهٔ گیرنده" htmlFor="sms-to">
          <Input id="sms-to" dir="ltr" inputMode="numeric" value={to} onChange={(e) => setTo(e.target.value)} placeholder="09xxxxxxxxx" />
        </Field>
        <Field label="سرویس" htmlFor="sms-provider">
          <NativeSelect id="sms-provider" value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="kavenegar">کاوه‌نگار</option>
            <option value="melipayamak">ملی‌پیامک</option>
          </NativeSelect>
        </Field>
        <Field
          label="متن پیامک"
          htmlFor="sms-message"
          className="sm:col-span-2"
          hint={
            seg.length === 0
              ? "۷۰ کاراکتر برای یک بخش"
              : `${faNum(seg.length)} کاراکتر — ${faNum(seg.parts)} بخش (${faNum(seg.perPart)} کاراکتر در هر بخش)`
          }
        >
          <Textarea id="sms-message" rows={4} value={message} onChange={(e) => setMessage(e.target.value)} />
        </Field>
        <div className="sm:col-span-2">
          <Button type="submit" disabled={busy} className="gap-1.5">
            {busy ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
            ارسال
          </Button>
        </div>
      </form>
    </Section>
  );
}

function LogsSection() {
  const q = useQuery({
    queryKey: QKEY,
    queryFn: () => api<{ items: SmsLog[]; total: number }>("/crm/sms/logs?limit=50"),
  });

  return (
    <Section
      title="تاریخچهٔ ارسال"
      hint={q.data ? `${faNum(q.data.total)} پیامک` : undefined}
      action={
        <Button variant="ghost" size="icon" className="size-8" aria-label="به‌روزرسانی" onClick={() => void q.refetch()}>
          <RefreshCw className={cn("size-4", q.isFetching && "animate-spin")} />
        </Button>
      }
    >
      {q.isPending ? (
        <ListSkeleton />
      ) : q.isError ? (
        <ErrorNote error={q.error} />
      ) : q.data.items.length === 0 ? (
        <Empty icon={MessageSquareText}>هنوز پیامکی ارسال نشده است.</Empty>
      ) : (
        <Table aria-label="تاریخچهٔ پیامک">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead>گیرنده</TableHead>
              <TableHead>سرویس</TableHead>
              <TableHead>متن</TableHead>
              <TableHead>وضعیت</TableHead>
              <TableHead>زمان</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {q.data.items.map((s) => (
              <TableRow key={s.id}>
                <TableCell className="tabular">{s.to_number}</TableCell>
                <TableCell>{PROVIDER_LABEL[s.provider] ?? s.provider}</TableCell>
                <TableCell className="max-w-[280px] truncate" title={s.message}>{s.message}</TableCell>
                <TableCell>
                  <ToneBadge tone={s.status === "sent" ? "success" : "danger"}>{s.status === "sent" ? "ارسال شد" : "خطا"}</ToneBadge>
                </TableCell>
                <TableCell className="tabular">
                  {s.sent_at ? faDate(new Date(s.sent_at), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Section>
  );
}

export function SmsTab() {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader icon={MessageSquareText} title="پیامک" hint="ارسال دستی و تاریخچهٔ پیامک‌های دفتر" />
      <Reveal><SendForm /></Reveal>
      <Reveal delay={0.06}><LogsSection /></Reveal>
    </div>
  );
}
