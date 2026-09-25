"use client";

// برند و سایت — root-only. Every field of GET/PUT /api/settings/site
// (app/api/routes/site.py), kept in AppSetting as one JSON row: brand name
// (fa/latin), tagline, office name, domain and contact details. A live
// preview mirrors the login page's own brand corner (src/app/panel/login/
// page.tsx), so root sees exactly what changes before saving.

import { Loader2, Palette } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Empty, ErrorNote, Field, PageHeader, Section } from "@/components/panel/kit";
import { Reveal, Tilt } from "@/components/viz";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { can, useSession } from "@/lib/session";

type SiteConfig = {
  brandName: string; brandNameLatin: string; tagline: string; agencyName: string; domain: string;
  phone: string; email: string; telegram: string; instagram: string; address: string;
  seoTitle: string; seoDescription: string;
};
type FieldKey = keyof SiteConfig;

const QKEY = ["settings", "site"] as const;

const TEXT_FIELDS: { key: FieldKey; label: string; hint?: string; max: number }[] = [
  { key: "brandName", label: "نام برند (فارسی)", max: 80 },
  { key: "brandNameLatin", label: "نام برند (لاتین)", max: 80 },
  { key: "tagline", label: "شعار زیر نام برند", max: 160 },
  { key: "agencyName", label: "نام دفتر/آژانس", max: 120 },
  { key: "domain", label: "دامنه", hint: "مثل sorinflow.com", max: 120 },
  { key: "phone", label: "شمارهٔ تماس", max: 40 },
  { key: "email", label: "ایمیل تماس", max: 120 },
  { key: "telegram", label: "آیدی تلگرام", max: 120 },
  { key: "instagram", label: "آیدی اینستاگرام", max: 120 },
];

function LivePreview({ site }: { site: SiteConfig }) {
  return (
    <div className="overflow-hidden rounded-2xl border bg-[#07070d] p-6 text-white">
      <div className="mb-8 flex items-center gap-2.5">
        <div
          aria-hidden
          className="grid size-10 place-items-center rounded-xl bg-linear-to-br from-indigo-500 to-violet-600 text-base font-black text-white shadow-[0_0_24px_-4px_rgb(99_102_241/0.7)]"
        >
          {(site.brandNameLatin || "S").slice(0, 1)}
        </div>
        <div className="leading-tight">
          <div className="text-lg font-extrabold">{site.brandName || "—"}</div>
          <div className="text-xs text-white/60">{site.tagline || "—"}</div>
        </div>
      </div>
      <h2 className="text-xl leading-[1.5] font-black">
        همهٔ فایل‌ها، مشتری‌ها و تماس‌های {site.agencyName || "دفتر"} در یک جا.
      </h2>
      <p className="mt-4 text-xs text-white/50" dir="ltr">{site.domain || "—"}</p>
    </div>
  );
}

function Form({ initial }: { initial: SiteConfig }) {
  const qc = useQueryClient();
  const [site, setSite] = useState<SiteConfig>(initial);
  // What the server last confirmed, so a save sends only the fields that
  // actually changed (PUT /settings/site applies exclude_unset fields).
  // Sending every field back unconditionally would also resubmit domain
  // untouched — and a bare "localhost" (this dev box's DOMAIN=localhost)
  // fails the backend's domain\.tld pattern, 422ing a save that never
  // touched that field at all.
  const [baseline, setBaseline] = useState<SiteConfig>(initial);
  const [busy, setBusy] = useState(false);

  function set<K extends FieldKey>(key: K, value: SiteConfig[K]) {
    setSite((s) => ({ ...s, [key]: value }));
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const changes = Object.fromEntries(
      (Object.keys(site) as FieldKey[]).filter((k) => site[k] !== baseline[k]).map((k) => [k, site[k]]),
    );
    if (Object.keys(changes).length === 0) {
      toast.error("چیزی تغییر نکرده است");
      return;
    }
    setBusy(true);
    try {
      const saved = await api<SiteConfig>("/settings/site", { method: "PUT", json: changes });
      setSite(saved);
      setBaseline(saved);
      await qc.invalidateQueries({ queryKey: QKEY });
      toast.success("ذخیره شد");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "ذخیره نشد");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
      <Reveal>
        <Section title="مشخصات برند و تماس">
          <form onSubmit={save} className="grid gap-3 sm:grid-cols-2">
            {TEXT_FIELDS.map((f) => (
              <Field key={f.key} label={f.label} htmlFor={`site-${f.key}`} hint={f.hint}>
                <Input
                  id={`site-${f.key}`}
                  dir={f.key === "domain" || f.key === "telegram" || f.key === "instagram" ? "ltr" : undefined}
                  maxLength={f.max}
                  value={site[f.key]}
                  onChange={(e) => set(f.key, e.target.value)}
                />
              </Field>
            ))}
            <Field label="نشانی" htmlFor="site-address" className="sm:col-span-2">
              <Input id="site-address" maxLength={300} value={site.address} onChange={(e) => set("address", e.target.value)} />
            </Field>
            <Field label="عنوان SEO" htmlFor="site-seo-title" className="sm:col-span-2" hint="عنوان صفحهٔ فرود">
              <Input id="site-seo-title" maxLength={120} value={site.seoTitle} onChange={(e) => set("seoTitle", e.target.value)} />
            </Field>
            <Field label="توضیح SEO" htmlFor="site-seo-desc" className="sm:col-span-2">
              <Textarea id="site-seo-desc" rows={3} maxLength={300} value={site.seoDescription} onChange={(e) => set("seoDescription", e.target.value)} />
            </Field>
            <div className="sm:col-span-2">
              <Button type="submit" disabled={busy} className="gap-1.5">
                {busy && <Loader2 className="size-4 animate-spin" />}
                ذخیرهٔ تنظیمات
              </Button>
            </div>
          </form>
        </Section>
      </Reveal>

      <Reveal delay={0.05}>
        <div className="flex flex-col gap-2">
          <div className="text-xs font-semibold text-muted-foreground">پیش‌نمایش زندهٔ صفحهٔ ورود</div>
          <Tilt max={4}>
            <LivePreview site={site} />
          </Tilt>
        </div>
      </Reveal>
    </div>
  );
}

export function SettingsPage() {
  const session = useSession();
  const user = session.data?.user;
  const q = useQuery({
    queryKey: QKEY,
    queryFn: () => api<{ site: SiteConfig; defaults: SiteConfig }>("/settings/site"),
    enabled: can(user, { roles: ["root"] }),
  });

  return (
    <div className="flex flex-col gap-5">
      <PageHeader icon={Palette} title="برند و سایت" hint="نام برند، دفتر، دامنه و اطلاعات تماس" />
      {session.isPending ? (
        <div className="h-40 animate-pulse rounded-2xl bg-muted/40" />
      ) : session.isError ? (
        <ErrorNote error={session.error} />
      ) : !can(user, { roles: ["root"] }) ? (
        <Empty>این بخش فقط برای root است.</Empty>
      ) : q.isPending ? (
        <div className="h-64 animate-pulse rounded-2xl bg-muted/40" />
      ) : q.isError ? (
        <ErrorNote error={q.error} />
      ) : (
        <Form initial={q.data.site} />
      )}
    </div>
  );
}
