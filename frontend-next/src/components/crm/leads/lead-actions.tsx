"use client";

// What can be done to one lead from anywhere (a row's menu, the drawer):
// notify, delete, convert to a deal, change status. Each confirms first
// where the old panel did, and refreshes every list that shows the lead.

import { Handshake, Trash2 } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useConfirm } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { faNum } from "@/lib/format";
import type { Lead } from "./types";

const errText = (e: unknown) => (e instanceof ApiError ? e.message : undefined);

export function useLeadActions() {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const router = useRouter();
  const refresh = (id?: number) => {
    qc.invalidateQueries({ queryKey: ["crm", "leads"] });
    qc.invalidateQueries({ queryKey: ["crm", "calls"] });
    if (id) {
      qc.invalidateQueries({ queryKey: ["crm", "lead", id] });
      qc.invalidateQueries({ queryKey: ["crm", "activity", "lead", id] });
    }
  };

  const status = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) => api<Lead>(`/crm/leads/${id}`, { method: "PATCH", json: { status } }),
    onSuccess: (_d, v) => {
      toast.success("وضعیت لید تغییر کرد");
      refresh(v.id);
    },
    onError: (e) => {
      toast.error("وضعیت تغییر نکرد", errText(e));
      refresh();
    },
  });

  const notify = useMutation({
    mutationFn: (id: number) => api<{ success: boolean; channel?: string }>(`/crm/leads/${id}/notify`, { method: "POST" }),
    onSuccess: (r, id) => {
      if (r.success) toast.success("اطلاع‌رسانی انجام شد", r.channel ? `از طریق ${r.channel}` : undefined);
      else toast.info("کانال اطلاع‌رسانی تنظیم نشده");
      refresh(id);
    },
    onError: (e) => toast.error("اطلاع‌رسانی انجام نشد", errText(e)),
  });

  const del = useMutation({
    mutationFn: (id: number) => api(`/crm/leads/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("لید حذف شد");
      refresh();
    },
    onError: (e) => toast.error("حذف نشد", errText(e)),
  });

  const convert = useMutation({
    mutationFn: (id: number) => api<{ deal: { id: number } }>(`/crm/leads/${id}/convert-to-deal`, { method: "POST" }),
    onSuccess: (r, id) => {
      toast.success("معامله ساخته شد", `معاملهٔ #${faNum(r.deal.id)}`);
      refresh(id);
      qc.invalidateQueries({ queryKey: ["crm", "deals"] });
    },
    onError: (e) => toast.error("معامله ساخته نشد", errText(e)),
  });

  return {
    setStatus: (id: number, s: string) => status.mutate({ id, status: s }),
    notify: (id: number) => notify.mutate(id),
    notifying: notify.isPending,
    /** resolves true once the lead is gone */
    remove: async (id: number) => {
      const ok = await confirm({
        title: "حذف لید",
        description: "این لید و ملکِ متصل به آن از همه‌جا (لیست املاک، یادداشت‌ها و تصاویر) حذف می‌شوند. این کار برگشت‌پذیر نیست.",
        confirm: "حذف",
        danger: true,
        icon: Trash2,
      });
      if (!ok) return false;
      try {
        await del.mutateAsync(id);
        return true;
      } catch {
        return false;
      }
    },
    convert: async (id: number) => {
      const ok = await confirm({ title: "تبدیل به معامله", description: "از این لید یک معامله ساخته شود؟", confirm: "بساز", icon: Handshake });
      if (!ok) return;
      try {
        await convert.mutateAsync(id);
        router.push("/panel/crm/deals");
      } catch {
        /* the toast said why */
      }
    },
  };
}
