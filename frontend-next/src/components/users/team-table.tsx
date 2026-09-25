"use client";

// اعضای تیم — the users table: search + role/active filters (client-side
// over one GET /users, same as the old panel — no server-side search params
// exist for this route), contact verification (root gets inline toggles,
// everyone else a read-only tick + nudge link), and the per-row kebab menu.

import {
  Ban, CheckCircle2, KeyRound, Loader2, MoreHorizontal, Plus, ShieldOff, Trash2, UserCog, UserRoundCheck,
} from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  Empty, ErrorNote, Field, ListSkeleton, NativeSelect, RingDialog, Section, ToneBadge, Toolbar, useConfirm,
} from "@/components/panel/kit";
import { Reveal } from "@/components/viz";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { faDate, faNum } from "@/lib/format";
import { displayName, ROLE_LABEL, type User } from "@/lib/session";
import { NewUserDialog } from "./new-user-dialog";
import { PermsEditorDialog } from "./perms-editor-dialog";

const QKEY = ["users", "team"] as const;

function ResetPasswordDialog({ open, onOpenChange, user }: { open: boolean; onOpenChange: (o: boolean) => void; user: User }) {
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);

  async function save() {
    if (pw.length < 6) {
      toast.error("رمز باید دست‌کم ۶ نویسه باشد");
      return;
    }
    setBusy(true);
    try {
      await api(`/users/${user.id}/password`, { method: "POST", json: { new_password: pw } });
      toast.success("رمز عبور تغییر کرد");
      setPw("");
      onOpenChange(false);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "ذخیره نشد");
    } finally {
      setBusy(false);
    }
  }

  return (
    <RingDialog open={open} onOpenChange={onOpenChange} icon={KeyRound} title="تغییر رمز عبور"
      description={<>رمز تازه برای <b>{displayName(user)}</b></>}
    >
      <div className="flex flex-col gap-3">
        <Field label="رمز تازه" htmlFor="rp-pw" hint="دست‌کم ۶ نویسه">
          <Input id="rp-pw" dir="ltr" type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus />
        </Field>
        <Button disabled={busy} onClick={save}>
          {busy && <Loader2 className="size-4 animate-spin" />}
          ذخیره
        </Button>
      </div>
    </RingDialog>
  );
}

function VerifyTick({
  set, verified, canToggle, onToggle, onNudge,
}: { set: boolean; verified: boolean; canToggle: boolean; onToggle: () => void; onNudge: () => void }) {
  if (!set) return <span className="text-muted-foreground">—</span>;
  if (canToggle) {
    return <Switch checked={verified} onCheckedChange={onToggle} size="sm" aria-label="تأییدشده" />;
  }
  return verified ? (
    <CheckCircle2 className="size-4 text-success" aria-label="تأیید شده" />
  ) : (
    <button type="button" onClick={onNudge} className="text-xs font-semibold text-warning hover:underline" title="درخواست تأیید">
      تأیید نشده
    </button>
  );
}

export function TeamTable({ me }: { me: User }) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [activeFilter, setActiveFilter] = useState("");
  const [newOpen, setNewOpen] = useState(false);
  const [permsUser, setPermsUser] = useState<User | null>(null);
  const [pwUser, setPwUser] = useState<User | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const q = useQuery({ queryKey: QKEY, queryFn: () => api<{ items: User[]; total: number }>("/users") });

  const rows = useMemo(() => {
    let items = q.data?.items ?? [];
    if (roleFilter) items = items.filter((u) => u.role === roleFilter);
    if (activeFilter) items = items.filter((u) => (activeFilter === "active" ? u.is_active : !u.is_active));
    const s = search.trim().toLowerCase();
    if (s) {
      items = items.filter((u) =>
        u.username.toLowerCase().includes(s) || (u.full_name || "").toLowerCase().includes(s)
        || (u.email || "").toLowerCase().includes(s) || (u.phone || "").includes(s));
    }
    return items;
  }, [q.data, roleFilter, activeFilter, search]);

  async function refresh() {
    await qc.invalidateQueries({ queryKey: QKEY });
  }

  async function setVerification(u: User, field: "phone_verified" | "email_verified", value: boolean) {
    setBusyId(u.id);
    try {
      await api(`/users/${u.id}/verification`, { method: "PATCH", json: { [field]: value } });
      await refresh();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "ذخیره نشد");
    } finally {
      setBusyId(null);
    }
  }

  async function nudge(u: User) {
    setBusyId(u.id);
    try {
      const r = await api<{ message: string }>(`/users/${u.id}/verification-request`, { method: "POST" });
      toast.success(r.message);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "ارسال نشد");
    } finally {
      setBusyId(null);
    }
  }

  async function toggleActive(u: User) {
    const next = !u.is_active;
    if (!(await confirm({
      title: next ? "فعال‌سازی کاربر" : "غیرفعال‌سازی کاربر", danger: !next,
      description: <>{displayName(u)} {next ? "دوباره می‌تواند وارد شود." : "دیگر نمی‌تواند وارد شود."}</>,
      confirm: next ? "فعال کن" : "غیرفعال کن", icon: Ban,
    }))) return;
    setBusyId(u.id);
    try {
      await api(`/users/${u.id}`, { method: "PATCH", json: { is_active: next } });
      await refresh();
      toast.success(next ? "فعال شد" : "غیرفعال شد");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "ذخیره نشد");
    } finally {
      setBusyId(null);
    }
  }

  async function disableTotp(u: User) {
    if (!(await confirm({ title: "غیرفعال‌سازی احراز هویت دو مرحله‌ای", description: <>2FA حساب {displayName(u)} خاموش می‌شود.</>, confirm: "غیرفعال کن", icon: ShieldOff }))) return;
    setBusyId(u.id);
    try {
      await api(`/users/${u.id}/totp/disable`, { method: "POST" });
      toast.success("غیرفعال شد");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "ناموفق بود");
    } finally {
      setBusyId(null);
    }
  }

  async function remove(u: User) {
    if (!(await confirm({ title: "حذف کاربر", danger: true, confirm: "حذف", icon: Trash2, description: <>حساب <b>{displayName(u)}</b> برای همیشه حذف شود؟</> }))) return;
    setBusyId(u.id);
    try {
      await api(`/users/${u.id}`, { method: "DELETE" });
      await refresh();
      toast.success("حذف شد");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "حذف نشد");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <Reveal delay={0.2}>
      <Section
        title="اعضای تیم"
        hint={q.data ? `${faNum(rows.length)} از ${faNum(q.data.total)}` : undefined}
        action={<Button size="sm" className="gap-1.5" onClick={() => setNewOpen(true)}><Plus className="size-4" /> کاربر تازه</Button>}
      >
        <Toolbar className="mb-4">
          <Field label="جستجو" htmlFor="team-search" className="w-52">
            <Input id="team-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="نام، ایمیل یا شماره" />
          </Field>
          <Field label="نقش" htmlFor="team-role" className="w-40">
            <NativeSelect id="team-role" value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)}>
              <option value="">همه</option>
              <option value="root">Root</option>
              <option value="super_admin">مدیر ارشد</option>
              <option value="admin">مدیر</option>
              <option value="visitor">بازدیدکننده</option>
            </NativeSelect>
          </Field>
          <Field label="وضعیت" htmlFor="team-active" className="w-36">
            <NativeSelect id="team-active" value={activeFilter} onChange={(e) => setActiveFilter(e.target.value)}>
              <option value="">همه</option>
              <option value="active">فعال</option>
              <option value="inactive">غیرفعال</option>
            </NativeSelect>
          </Field>
        </Toolbar>

        {q.isPending ? (
          <ListSkeleton rows={5} />
        ) : q.isError ? (
          <ErrorNote error={q.error} />
        ) : rows.length === 0 ? (
          <Empty icon={UserCog}>کاربری با این فیلتر پیدا نشد.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] border-collapse text-sm">
              <thead>
                <tr className="border-b text-start text-xs text-muted-foreground">
                  <th className="p-2 text-start font-medium">کاربر</th>
                  <th className="p-2 text-start font-medium">نقش</th>
                  <th className="p-2 text-start font-medium">تماس</th>
                  <th className="p-2 text-start font-medium">وضعیت</th>
                  <th className="p-2 text-start font-medium">آخرین ورود</th>
                  <th className="p-2 text-center font-medium">عملیات</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((u) => {
                  const canTouch = me.role === "root" || u.role !== "root";
                  const isSelf = u.id === me.id;
                  const rootToggle = me.role === "root";
                  return (
                    <tr key={u.id} className="border-b last:border-0 align-top">
                      <td className="p-2">
                        <div className="flex items-center gap-2">
                          <Avatar size="sm">
                            {u.avatar_url && <AvatarImage src={u.avatar_url} alt="" />}
                            <AvatarFallback>{(displayName(u) || "?").charAt(0)}</AvatarFallback>
                          </Avatar>
                          <div className="min-w-0">
                            <div className="truncate font-semibold">{displayName(u)}</div>
                            <div className="truncate text-xs text-muted-foreground">{u.headline || `@${u.username}`}</div>
                          </div>
                        </div>
                      </td>
                      <td className="p-2">
                        <ToneBadge tone={u.role === "root" ? "violet" : u.role === "super_admin" ? "primary" : "neutral"}>
                          {ROLE_LABEL[u.role]}
                        </ToneBadge>
                        {u.role === "admin" && (
                          <div className="mt-1 text-[11px] text-muted-foreground">{faNum(u.permissions.length)} دسترسی</div>
                        )}
                      </td>
                      <td className="p-2">
                        <div className="flex flex-col gap-1">
                          <div className="flex items-center gap-1.5">
                            <span dir="ltr" className="text-xs">{u.email || "—"}</span>
                            <VerifyTick
                              set={!!u.email} verified={u.email_verified} canToggle={rootToggle && !!u.email}
                              onToggle={() => setVerification(u, "email_verified", !u.email_verified)}
                              onNudge={() => nudge(u)}
                            />
                          </div>
                          <div className="flex items-center gap-1.5">
                            <span dir="ltr" className="text-xs">{u.phone || "—"}</span>
                            <VerifyTick
                              set={!!u.phone} verified={u.phone_verified} canToggle={rootToggle && !!u.phone}
                              onToggle={() => setVerification(u, "phone_verified", !u.phone_verified)}
                              onNudge={() => nudge(u)}
                            />
                          </div>
                        </div>
                      </td>
                      <td className="p-2">
                        <ToneBadge tone={u.is_active ? "success" : "neutral"}>{u.is_active ? "فعال" : "غیرفعال"}</ToneBadge>
                      </td>
                      <td className="p-2 text-xs text-muted-foreground">
                        {u.last_login ? faDate(new Date(u.last_login), { month: "short", day: "numeric" }) : "—"}
                      </td>
                      <td className="p-2 text-center">
                        <DropdownMenu dir="rtl">
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon" className="size-8" disabled={busyId === u.id} aria-label="عملیات کاربر">
                              {busyId === u.id ? <Loader2 className="size-4 animate-spin" /> : <MoreHorizontal className="size-4" />}
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="start" className="w-52">
                            <DropdownMenuItem disabled={!canTouch} onSelect={() => setPermsUser(u)}>
                              <UserCog /> نقش و دسترسی‌ها
                            </DropdownMenuItem>
                            <DropdownMenuItem disabled={!canTouch || isSelf} onSelect={() => setPwUser(u)}>
                              <KeyRound /> تغییر رمز
                            </DropdownMenuItem>
                            <DropdownMenuItem disabled={!canTouch} onSelect={() => disableTotp(u)}>
                              <ShieldOff /> غیرفعال‌سازی 2FA
                            </DropdownMenuItem>
                            <DropdownMenuItem disabled={!canTouch || isSelf} onSelect={() => toggleActive(u)}>
                              {u.is_active ? <Ban /> : <UserRoundCheck />}
                              {u.is_active ? "غیرفعال کردن" : "فعال کردن"}
                            </DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem variant="destructive" disabled={!canTouch || isSelf} onSelect={() => remove(u)}>
                              <Trash2 /> حذف
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <NewUserDialog open={newOpen} onOpenChange={setNewOpen} actorRole={me.role} />
      {permsUser && <PermsEditorDialog open onOpenChange={(o) => !o && setPermsUser(null)} user={permsUser} actorRole={me.role} />}
      {pwUser && <ResetPasswordDialog open onOpenChange={(o) => !o && setPwUser(null)} user={pwUser} />}
    </Reveal>
  );
}
