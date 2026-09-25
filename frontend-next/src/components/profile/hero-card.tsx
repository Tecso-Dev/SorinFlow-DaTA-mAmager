"use client";

// Hero card: avatar (upload/remove), presence, role/member-since/last-login,
// and «IP شما از نگاه سرور» — a sanity check that per-address rate limits see
// real callers behind the ingress, not the cluster's own address.

import { Camera, MapPin, Trash2 } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { NativeSelect, Section } from "@/components/panel/kit";
import { Reveal, Tilt } from "@/components/viz";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { faDate } from "@/lib/format";
import { ROLE_LABEL, SESSION_KEY, displayName, type User } from "@/lib/session";

const PRESENCE_FA: Record<User["presence"], string> = {
  available: "در دسترس", busy: "مشغول", away: "دور از میز",
};
const PRESENCE_DOT: Record<User["presence"], string> = {
  available: "bg-success", busy: "bg-destructive", away: "bg-warning",
};

function since(iso: string | null) {
  if (!iso) return "—";
  return faDate(new Date(iso), { year: "numeric", month: "long", day: "numeric" });
}

export function HeroCard({ me }: { me: User }) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [savingPresence, setSavingPresence] = useState(false);

  const ip = useQuery({ queryKey: ["profile", "ip"], queryFn: () => api<{ ip: string }>("/users/me/ip") });

  async function refresh() {
    await qc.invalidateQueries({ queryKey: SESSION_KEY });
  }

  async function onUpload(file: File | undefined) {
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) {
      toast.error("حجم تصویر باید کمتر از ۵ مگابایت باشد");
      return;
    }
    const fd = new FormData();
    fd.append("file", file);
    setUploading(true);
    try {
      await api("/users/me/avatar", { method: "POST", body: fd });
      await refresh();
      toast.success("عکس عوض شد");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "آپلود ناموفق بود");
    } finally {
      setUploading(false);
    }
  }

  async function removeAvatar() {
    setUploading(true);
    try {
      await api("/users/me/avatar", { method: "DELETE" });
      await refresh();
      toast.success("عکس حذف شد");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "حذف نشد");
    } finally {
      setUploading(false);
    }
  }

  async function setPresence(value: string) {
    setSavingPresence(true);
    try {
      await api("/users/me", { method: "PATCH", json: { presence: value } });
      await refresh();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "ذخیره نشد");
    } finally {
      setSavingPresence(false);
    }
  }

  return (
    <Reveal>
      <Section className="overflow-hidden">
        <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-32 bg-[radial-gradient(60%_100%_at_50%_0%,var(--glow-1),transparent)]" />
        <div className="relative flex flex-col items-center gap-4 text-center sm:flex-row sm:items-end sm:text-start">
          <Tilt className="shrink-0" max={10}>
            <div className="group relative">
              <Avatar className="size-24 shadow-[0_16px_36px_-14px_rgb(99_102_241/0.6)]" size="lg">
                {me.avatar_url && <AvatarImage src={me.avatar_url} alt="" />}
                <AvatarFallback className="text-2xl font-black">
                  {(displayName(me) || "?").trim().charAt(0)}
                </AvatarFallback>
              </Avatar>
              <span
                className={`absolute bottom-1 start-1 size-4 rounded-full ring-2 ring-card ${PRESENCE_DOT[me.presence]}`}
                aria-hidden
              />
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                disabled={uploading}
                aria-label="تغییر عکس پروفایل"
                className="absolute inset-0 grid place-items-center rounded-full bg-black/0 text-white opacity-0 transition-opacity group-hover:bg-black/40 group-hover:opacity-100 focus-visible:opacity-100 disabled:opacity-100"
              >
                <Camera className="size-6" />
              </button>
              <input
                ref={fileRef}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                className="sr-only"
                onChange={(e) => onUpload(e.target.files?.[0])}
              />
            </div>
          </Tilt>

          <div className="min-w-0 flex-1">
            <h1 className="text-xl font-black">{displayName(me)}</h1>
            <p className="mt-0.5 text-sm text-muted-foreground">{me.headline || "سمت خود را در «مشخصات» بنویسید"}</p>
            <div className="mt-2 flex flex-wrap items-center justify-center gap-2 sm:justify-start">
              <span className="rounded-full bg-primary/12 px-2 py-0.5 text-[11px] font-semibold text-primary">
                {ROLE_LABEL[me.role]}
              </span>
              <span dir="ltr" className="text-xs text-muted-foreground">@{me.username}</span>
            </div>
          </div>

          {me.avatar_url && (
            <Button variant="ghost" size="sm" className="gap-1.5 text-destructive" disabled={uploading} onClick={removeAvatar}>
              <Trash2 className="size-4" /> حذف عکس
            </Button>
          )}
        </div>

        <div className="relative mt-5 grid grid-cols-2 gap-3 border-t pt-4 sm:grid-cols-4">
          <div>
            <div className="text-xs text-muted-foreground">وضعیت</div>
            <NativeSelect
              className="mt-1 h-8 text-xs"
              aria-label="وضعیت حضور"
              value={me.presence}
              disabled={savingPresence}
              onChange={(e) => setPresence(e.target.value)}
            >
              {(Object.keys(PRESENCE_FA) as User["presence"][]).map((k) => (
                <option key={k} value={k}>{PRESENCE_FA[k]}</option>
              ))}
            </NativeSelect>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">عضویت از</div>
            <div className="mt-1.5 text-sm font-semibold tabular">{since(me.created_at)}</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">آخرین ورود</div>
            <div className="mt-1.5 text-sm font-semibold tabular">{since(me.last_login)}</div>
          </div>
          <div>
            <div className="flex items-center gap-1 text-xs text-muted-foreground">
              <MapPin className="size-3" /> IP شما از نگاه سرور
            </div>
            <div className="mt-1.5 text-sm font-semibold tabular" dir="ltr">
              {ip.isPending ? "…" : ip.isError ? "—" : ip.data.ip}
            </div>
          </div>
        </div>
      </Section>
    </Reveal>
  );
}
