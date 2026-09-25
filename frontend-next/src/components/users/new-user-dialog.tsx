"use client";

// کاربر تازه — super_admin creates staff directly (no self sign-up path
// here); root may also hand out super_admin.

import { Loader2, UserPlus } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Field, NativeSelect, RingDialog } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import type { Role } from "@/lib/session";
import { usePermCatalog } from "./permissions";

const QKEY = ["users", "team"] as const;

export function NewUserDialog({ open, onOpenChange, actorRole }: { open: boolean; onOpenChange: (o: boolean) => void; actorRole: Role }) {
  const qc = useQueryClient();
  const catalog = usePermCatalog();
  const [username, setUsername] = useState("");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("admin");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);

  const roleOptions: Role[] = actorRole === "root" ? ["admin", "super_admin", "visitor"] : ["admin", "visitor"];

  function toggle(key: string) {
    setPicked((s) => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  function reset() {
    setUsername(""); setFullName(""); setEmail(""); setPassword(""); setRole("admin"); setPicked(new Set());
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!username.trim() || password.length < 6) {
      toast.error("نام کاربری و رمز (دست‌کم ۶ نویسه) لازم است");
      return;
    }
    setBusy(true);
    try {
      await api("/users", {
        method: "POST",
        json: {
          username: username.trim(), full_name: fullName.trim() || undefined, email: email.trim() || undefined,
          password, role, permissions: role === "admin" ? [...picked] : undefined,
        },
      });
      await qc.invalidateQueries({ queryKey: QKEY });
      toast.success("کاربر ساخته شد");
      reset();
      onOpenChange(false);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "ساخت کاربر ناموفق بود");
    } finally {
      setBusy(false);
    }
  }

  return (
    <RingDialog open={open} onOpenChange={onOpenChange} icon={UserPlus} title="کاربر تازه" wide>
      <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
        <Field label="نام کاربری" htmlFor="nu-username">
          <Input id="nu-username" dir="ltr" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        </Field>
        <Field label="نام کامل" htmlFor="nu-fullname">
          <Input id="nu-fullname" value={fullName} onChange={(e) => setFullName(e.target.value)} />
        </Field>
        <Field label="ایمیل" htmlFor="nu-email">
          <Input id="nu-email" dir="ltr" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="رمز عبور" htmlFor="nu-password" hint="دست‌کم ۶ نویسه">
          <Input id="nu-password" dir="ltr" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Field label="نقش" htmlFor="nu-role" className="sm:col-span-2">
          <NativeSelect id="nu-role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {roleOptions.map((r) => (
              <option key={r} value={r}>
                {r === "admin" ? "مدیر — دسترسی‌های انتخابی" : r === "super_admin" ? "مدیر ارشد — دسترسی کامل" : "بازدیدکننده — فقط پورتال"}
              </option>
            ))}
          </NativeSelect>
        </Field>
        {role === "admin" && (
          <div className="sm:col-span-2">
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
        <div className="sm:col-span-2">
          <Button type="submit" disabled={busy} className="w-full gap-1.5">
            {busy && <Loader2 className="size-4 animate-spin" />}
            ساخت کاربر
          </Button>
        </div>
      </form>
    </RingDialog>
  );
}
