"use client";

// نقش و دسترسی‌ها — role + (for admin) the permission checklist.
//
// A real regression happened here in the old panel: the role select read
// u.role with a fallback to 'admin' for anything unrecognised, and root is
// recognised by nothing in that list — so opening this editor on a root
// account preselected «مدیر», and saving demoted it without anyone choosing
// to. The fix kept here: the select always starts on the account's ACTUAL
// role, root included, so saving without touching the field is a no-op.

import { Loader2, ShieldQuestion } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Checkbox } from "@/components/ui/checkbox";
import { NativeSelect, RingDialog } from "@/components/panel/kit";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import type { Role, User } from "@/lib/session";
import { usePermCatalog } from "./permissions";

const ROLE_LABEL: Record<Role, string> = {
  root: "Root — بالاترین سطح",
  super_admin: "مدیر ارشد — دسترسی کامل",
  admin: "مدیر — دسترسی‌های انتخابی",
  visitor: "بازدیدکننده — فقط پورتال",
};

const QKEY = ["users", "team"] as const;

export function PermsEditorDialog({
  open, onOpenChange, user, actorRole,
}: { open: boolean; onOpenChange: (o: boolean) => void; user: User; actorRole: Role }) {
  const qc = useQueryClient();
  const catalog = usePermCatalog();
  const assignable: Role[] = actorRole === "root" ? ["root", "super_admin", "admin", "visitor"] : ["admin", "visitor"];
  const options = assignable.includes(user.role) ? assignable : [user.role, ...assignable];

  const [role, setRole] = useState<Role>(user.role);
  const [picked, setPicked] = useState<Set<string>>(new Set(user.permissions));
  const [busy, setBusy] = useState(false);

  function toggle(key: string) {
    setPicked((s) => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  async function save() {
    setBusy(true);
    try {
      await api(`/users/${user.id}`, {
        method: "PATCH",
        json: { role, permissions: role === "admin" ? [...picked] : [] },
      });
      await qc.invalidateQueries({ queryKey: QKEY });
      toast.success("دسترسی‌ها بروزرسانی شد");
      onOpenChange(false);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "ذخیره نشد");
    } finally {
      setBusy(false);
    }
  }

  return (
    <RingDialog open={open} onOpenChange={onOpenChange} icon={ShieldQuestion} title="نقش و دسترسی‌ها" wide
      description={<><b>{user.full_name || user.username}</b> <span dir="ltr">{user.phone || ""}</span></>}
    >
      <div className="flex flex-col gap-3">
        <NativeSelect aria-label="نقش" value={role} onChange={(e) => setRole(e.target.value as Role)}>
          {options.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
        </NativeSelect>
        {user.role === "root" && (
          <p className="rounded-lg bg-warning/12 px-3 py-2 text-xs text-warning">
            این حساب Root است. اگر نقش را عوض کنید، دیگر نمی‌توانید از این صفحه برش گردانید — فقط از طریق پایگاه داده.
          </p>
        )}
        {role === "admin" && (
          <div>
            <div className="mb-1.5 text-xs font-semibold text-muted-foreground">دسترسی‌ها</div>
            <div className="flex flex-wrap gap-x-4 gap-y-2">
              {catalog.data?.items.map((p) => (
                <label key={p.key} className="flex cursor-pointer items-center gap-1.5 text-sm">
                  <Checkbox checked={picked.has(p.key)} onCheckedChange={() => toggle(p.key)} />
                  {p.label}
                </label>
              ))}
            </div>
          </div>
        )}
        <Button className="w-full" disabled={busy} onClick={save}>
          {busy && <Loader2 className="size-4 animate-spin" />}
          ذخیره
        </Button>
      </div>
    </RingDialog>
  );
}
