"use client";

// درخواست‌های دسترسی به پنل — visitors asking to become admin. Approving
// picks the permission set (defaults to DEFAULT_ADMIN_PERMISSIONS if none
// ticked); rejecting clears it. super_admin only.

import { Check, Inbox, Loader2, X } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Empty, ErrorNote, ListSkeleton, Section } from "@/components/panel/kit";
import { Reveal } from "@/components/viz";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { usePermCatalog } from "./permissions";

type Ticket = {
  id: number; user_id: number; message: string | null; contact_phone: string | null;
  status: string; created_at: string | null;
  user: { id: number; full_name: string | null; phone: string | null; email: string | null } | null;
};

const QKEY = ["users", "tickets"] as const;

function TicketRow({ ticket }: { ticket: Ticket }) {
  const qc = useQueryClient();
  const catalog = usePermCatalog();
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);

  function toggle(key: string) {
    setPicked((s) => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  async function decide(approve: boolean) {
    setBusy(approve ? "approve" : "reject");
    try {
      await api(`/portal/admin/tickets/${ticket.id}/decide`, {
        method: "POST",
        json: { approve, permissions: approve ? [...picked] : undefined },
      });
      await qc.invalidateQueries({ queryKey: QKEY });
      toast.success(approve ? "درخواست تأیید شد" : "درخواست رد شد");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "ثبت تصمیم ناموفق بود");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-xl border p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-sm font-bold">{ticket.user?.full_name || ticket.user?.phone || "—"}</div>
          <div className="text-xs text-muted-foreground" dir="ltr">
            {ticket.contact_phone || ticket.user?.phone || ""} {ticket.user?.email ? `— ${ticket.user.email}` : ""}
          </div>
        </div>
      </div>
      {ticket.message && <p className="text-sm text-muted-foreground">{ticket.message}</p>}
      <div>
        <div className="mb-1.5 text-xs font-semibold text-muted-foreground">دسترسی‌ها برای تأیید</div>
        <div className="flex flex-wrap gap-x-4 gap-y-2">
          {catalog.data?.items.map((p) => (
            <label key={p.key} className="flex cursor-pointer items-center gap-1.5 text-sm">
              <Checkbox checked={picked.has(p.key)} onCheckedChange={() => toggle(p.key)} />
              {p.label}
            </label>
          ))}
        </div>
      </div>
      <div className="flex gap-2">
        <Button size="sm" className="gap-1.5" disabled={!!busy} onClick={() => decide(true)}>
          {busy === "approve" ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}
          تأیید
        </Button>
        <Button size="sm" variant="outline" className="gap-1.5 text-destructive" disabled={!!busy} onClick={() => decide(false)}>
          {busy === "reject" ? <Loader2 className="size-4 animate-spin" /> : <X className="size-4" />}
          رد
        </Button>
      </div>
    </div>
  );
}

export function TicketsCard() {
  const q = useQuery({
    queryKey: QKEY,
    queryFn: () => api<{ items: Ticket[]; total: number }>("/portal/admin/tickets?status=pending"),
  });

  return (
    <Reveal>
      <Section title="درخواست‌های دسترسی به پنل" hint={q.data ? `${q.data.total} درخواست در انتظار` : undefined}>
        {q.isPending ? (
          <ListSkeleton />
        ) : q.isError ? (
          <ErrorNote error={q.error} />
        ) : q.data.items.length === 0 ? (
          <Empty icon={Inbox}>درخواست در انتظاری نیست.</Empty>
        ) : (
          <div className="flex flex-col gap-3">
            {q.data.items.map((t) => <TicketRow key={t.id} ticket={t} />)}
          </div>
        )}
      </Section>
    </Reveal>
  );
}
