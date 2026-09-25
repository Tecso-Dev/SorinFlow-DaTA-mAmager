"use client";

// رویدادها — the audit trail. root/super_admin only (enforced server-side;
// the nav item is already role-gated). Actor search + action filter + Jalali
// since/until, 50/page.

import { ScrollText } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { JalaliDateInput } from "@/components/panel/date-input";
import {
  Empty, ErrorNote, Field, ListSkeleton, NativeSelect, PageHeader, Pagination, Section, ToneBadge, Toolbar,
} from "@/components/panel/kit";
import { Reveal } from "@/components/viz";
import { can, useSession } from "@/lib/session";
import { api } from "@/lib/api";
import { qs } from "@/lib/crm";
import { faDate, faNum } from "@/lib/format";

type AuditEvent = {
  id: number; created_at: string | null; actor_user_id: number | null; actor_username: string | null;
  actor_role: string | null; action: string; action_label: string; target_type: string | null;
  target_id: number | null; summary: string | null; detail: unknown; ip: string | null; request_id: string | null;
};

const QKEY = ["audit", "events"] as const;
const PAGE_SIZE = 50;

function when(iso: string | null) {
  if (!iso) return "—";
  return faDate(new Date(iso), { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** A bare Gregorian date (YYYY-MM-DD) — the backend treats it specially:
 *  `until` means through the end of that day. */
function bareDate(d: Date | null): string {
  if (!d) return "";
  const y = d.getFullYear(), m = String(d.getMonth() + 1).padStart(2, "0"), day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function AuditTable() {
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const [since, setSince] = useState<Date | null>(null);
  const [until, setUntil] = useState<Date | null>(null);
  const [page, setPage] = useState(1);

  const actions = useQuery({
    queryKey: ["audit", "actions"],
    queryFn: () => api<{ items: { key: string; label: string }[] }>("/audit/actions"),
    staleTime: 5 * 60_000,
  });

  const q = useQuery({
    queryKey: [...QKEY, actor, action, since?.getTime(), until?.getTime(), page],
    queryFn: () => api<{ items: AuditEvent[]; total: number }>(
      `/audit/events${qs({
        actor, action, since: bareDate(since), until: bareDate(until),
        limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE,
      })}`,
    ),
  });

  function setPage1(fn: () => void) {
    fn();
    setPage(1);
  }

  const pages = q.data ? Math.max(1, Math.ceil(q.data.total / PAGE_SIZE)) : 1;

  return (
    <Reveal>
      <Section title="رویدادها" hint={q.data ? `${faNum(q.data.total)} رویداد` : undefined}>
        <Toolbar className="mb-4">
          <Field label="کاربر" htmlFor="audit-actor" className="w-44">
            <Input id="audit-actor" value={actor} onChange={(e) => setPage1(() => setActor(e.target.value))} placeholder="نام کاربری" />
          </Field>
          <Field label="عملکرد" htmlFor="audit-action" className="w-52">
            <NativeSelect id="audit-action" value={action} onChange={(e) => setPage1(() => setAction(e.target.value))}>
              <option value="">همه</option>
              {actions.data?.items.map((a) => <option key={a.key} value={a.key}>{a.label}</option>)}
            </NativeSelect>
          </Field>
          <Field label="از تاریخ">
            <JalaliDateInput value={since} onChange={(d) => setPage1(() => setSince(d))} aria-label="از تاریخ" />
          </Field>
          <Field label="تا تاریخ">
            <JalaliDateInput value={until} onChange={(d) => setPage1(() => setUntil(d))} aria-label="تا تاریخ" />
          </Field>
        </Toolbar>

        {q.isPending ? (
          <ListSkeleton />
        ) : q.isError ? (
          <ErrorNote error={q.error} />
        ) : q.data.items.length === 0 ? (
          <Empty icon={ScrollText}>رویدادی با این فیلتر پیدا نشد.</Empty>
        ) : (
          <>
            <div className="overflow-x-auto rounded-lg border">
              <Table aria-label="فهرست رویدادها">
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead>زمان</TableHead>
                    <TableHead>کاربر</TableHead>
                    <TableHead>عملکرد</TableHead>
                    <TableHead>هدف</TableHead>
                    <TableHead>IP</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {q.data.items.map((e) => (
                    <TableRow key={e.id}>
                      <TableCell className="whitespace-nowrap tabular">{when(e.created_at)}</TableCell>
                      <TableCell>
                        <div className="font-medium">{e.actor_username || "—"}</div>
                        {e.actor_role && <div className="text-[11px] text-muted-foreground">{e.actor_role}</div>}
                      </TableCell>
                      <TableCell>
                        <ToneBadge tone="neutral">{e.action_label}</ToneBadge>
                        {e.summary && <div className="mt-1 max-w-[280px] truncate text-xs text-muted-foreground" title={e.summary}>{e.summary}</div>}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {e.target_type ? `${e.target_type}${e.target_id ? ` #${faNum(e.target_id)}` : ""}` : "—"}
                      </TableCell>
                      <TableCell dir="ltr" className="text-xs tabular text-muted-foreground">{e.ip || "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <div className="mt-4">
              <Pagination page={page} pages={pages} onPage={setPage} />
            </div>
          </>
        )}
      </Section>
    </Reveal>
  );
}

export function AuditPage() {
  const session = useSession();
  const user = session.data?.user;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader icon={ScrollText} title="رویدادها" hint="ثبت اقدام‌های مدیریتی روی پنل" />
      {session.isPending ? (
        <div className="h-40 animate-pulse rounded-2xl bg-muted/40" />
      ) : session.isError ? (
        <ErrorNote error={session.error} />
      ) : !can(user, { roles: ["root", "super_admin"] }) ? (
        <Empty>این بخش فقط برای root و مدیر ارشد است.</Empty>
      ) : (
        <AuditTable />
      )}
    </div>
  );
}
