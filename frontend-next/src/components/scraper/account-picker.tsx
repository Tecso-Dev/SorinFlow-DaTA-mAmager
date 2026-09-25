"use client";

// «شمارهٔ دیوار»: pick one of the caller's own numbers for this run, or
// leave it on «خودکار» (the server then picks the least-spent one), and the
// list of the caller's own numbers with an on/off switch each — a number
// switched off is skipped by rotation, «خودکار» and a manual pick alike.
// `mine=1` on purpose: even root only sees and uses their own numbers here.

import Link from "next/link";
import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Field, NativeSelect } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Switch } from "@/components/ui/switch";
import { api, ApiError } from "@/lib/api";
import { faNum } from "@/lib/format";
import { type CookiesResponse, divarUsable } from "./types";

export function useMyCookies() {
  return useQuery({
    queryKey: ["scraper", "cookies", "mine"],
    queryFn: () => api<CookiesResponse>("/auth/cookies?mine=1"),
    staleTime: 15_000,
  });
}

export function AccountPicker({ value, onChange, rotateEvery }: { value: string; onChange: (phone: string) => void; rotateEvery: number | undefined }) {
  const qc = useQueryClient();
  const cookies = useMyCookies();
  const rows = cookies.data?.cookies ?? [];

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api<{ success: boolean; id: number; phone_number: string; is_enabled: boolean; moved_jobs: string[] }>(`/auth/cookies/${id}`, {
        method: "PATCH",
        json: { enabled },
      }),
    onSuccess: (r) => {
      toast.success(
        r.is_enabled ? "روشن شد" : "خاموش شد",
        r.is_enabled
          ? `${r.phone_number} دوباره در چرخش است`
          : `${r.phone_number} دیگر استفاده نمی‌شود` +
            (r.moved_jobs.length ? ` — ${faNum(r.moved_jobs.length)} اسکرپ در حال اجرا به شمارهٔ دیگر شما منتقل می‌شود` : ""),
      );
      qc.invalidateQueries({ queryKey: ["scraper", "cookies"] });
      if (r.moved_jobs.length) qc.invalidateQueries({ queryKey: ["scraper", "jobs"] });
    },
    onError: (e) => toast.error("انجام نشد", e instanceof ApiError ? e.message : undefined),
  });

  // a pick that has since gone bad or been switched off falls back to «خودکار»
  const chosen = rows.find((c) => c.phone_number === value);
  const stale = !!value && rows.length > 0 && (!chosen || !divarUsable(chosen));
  useEffect(() => {
    if (stale) onChange("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stale]);

  let note = "کم‌مصرف‌ترین شمارهٔ شما انتخاب می‌شود.";
  let noteWarn = false;
  if (value) {
    noteWarn = rotateEvery !== 0;
    note = rotateEvery === 0
      ? "فقط از همین شماره استفاده می‌شود."
      : "با این حال چرخش شماره ممکن است وسط کار عوضش کند — برای ثابت ماندن، «چرخش شماره» را ۰ بگذارید.";
  }

  return (
    <Field label="شمارهٔ دیوار" htmlFor="scraper-account" hint={note} className={noteWarn ? "[&_p]:text-warning" : undefined}>
      <NativeSelect id="scraper-account" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">خودکار — کم‌مصرف‌ترین</option>
        {rows
          .slice()
          .sort((a, b) => (a.reveals || 0) - (b.reveals || 0))
          .map((c) => {
            const bits = [`${faNum(c.reveals || 0)} افشا`];
            if (c.is_enabled === false) bits.push("خاموش");
            if (!c.is_valid) bits.push("نامعتبر");
            if (c.identity_required_at) bits.push("احراز هویت لازم");
            else if (c.challenged_at) bits.push("اخیراً کد خواسته");
            return (
              <option key={c.id} value={c.phone_number} disabled={!divarUsable(c)}>
                {c.phone_number} — {bits.join("، ")}
              </option>
            );
          })}
      </NativeSelect>

      {rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          هیچ شمارهٔ دیواری به نام شما ثبت نشده — از{" "}
          {/* a link inline in a sentence needs more than colour to read as a
              link (axe: link-in-text-block) — kept underlined, not just on hover */}
          <Link href="/panel/divar" className="text-primary underline underline-offset-2 hover:no-underline">
            احراز هویت دیوار
          </Link>{" "}
          شمارهٔ خودتان را وارد کنید.
        </p>
      ) : (
        <ul className="grid gap-1" aria-label="شماره‌های دیوار من">
          {rows.map((c) => {
            const on = c.is_enabled !== false;
            const flag = !c.is_valid ? "نامعتبر" : c.identity_required_at ? "احراز هویت" : c.challenged_at ? "کد خواسته" : null;
            return (
              <li
                key={c.id}
                className="flex items-center gap-2 rounded-lg border bg-background/40 px-2.5 py-1.5 text-xs"
                title={on ? "روشن — در چرخش و «خودکار» استفاده می‌شود" : "خاموش — هیچ اسکرپی از این شماره استفاده نمی‌کند"}
              >
                <Switch
                  size="sm"
                  checked={on}
                  disabled={toggle.isPending}
                  onCheckedChange={(v) => toggle.mutate({ id: c.id, enabled: v })}
                  aria-label={`روشن/خاموش ${c.phone_number}`}
                />
                <span dir="ltr" className="font-semibold tabular">{c.phone_number}</span>
                <span className="text-muted-foreground">{faNum(c.reveals || 0)} افشا</span>
                {flag && <span className="ms-auto rounded-full bg-warning/15 px-1.5 py-0.5 font-semibold text-warning">{flag}</span>}
              </li>
            );
          })}
        </ul>
      )}
    </Field>
  );
}
