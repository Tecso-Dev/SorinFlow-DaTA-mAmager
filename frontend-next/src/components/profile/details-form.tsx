"use client";

// مشخصات — name, username (rename reissues the session), headline, bio, links.

import { IdCard, Loader2 } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Field, Section, useConfirm } from "@/components/panel/kit";
import { Reveal } from "@/components/viz";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { SESSION_KEY, type User } from "@/lib/session";

export function DetailsForm({ me }: { me: User }) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [fullName, setFullName] = useState(me.full_name ?? "");
  const [username, setUsername] = useState(me.username);
  const [headline, setHeadline] = useState(me.headline ?? "");
  const [bio, setBio] = useState(me.bio ?? "");
  const [website, setWebsite] = useState(me.links?.website ?? "");
  const [instagram, setInstagram] = useState(me.links?.instagram ?? "");
  const [linkedin, setLinkedin] = useState(me.links?.linkedin ?? "");
  const [busy, setBusy] = useState(false);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (username.trim() !== me.username) {
      const ok = await confirm({
        title: "تغییر نام کاربری",
        description: <>از این پس با <b dir="ltr">{username.trim()}</b> وارد می‌شوید. ادامه؟</>,
        confirm: "تغییر بده",
        icon: IdCard,
      });
      if (!ok) return;
    }
    setBusy(true);
    try {
      await api("/users/me", {
        method: "PATCH",
        json: {
          full_name: fullName.trim(), username: username.trim(),
          headline: headline.trim(), bio: bio.trim(),
          links: { website: website.trim(), instagram: instagram.trim(), linkedin: linkedin.trim() },
        },
      });
      await qc.invalidateQueries({ queryKey: SESSION_KEY });
      toast.success("ذخیره شد");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "ذخیره نشد");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Reveal delay={0.05}>
      <Section title="مشخصات" hint="نامی که همکاران در جاهای دیگر پنل می‌بینند">
        <form onSubmit={save} className="grid gap-3 sm:grid-cols-2">
          <Field label="نام کامل" htmlFor="pf-full-name">
            <Input id="pf-full-name" value={fullName} onChange={(e) => setFullName(e.target.value)} />
          </Field>
          <Field label="نام کاربری" htmlFor="pf-username" hint="برای ورود">
            <Input id="pf-username" dir="ltr" value={username} onChange={(e) => setUsername(e.target.value)} />
          </Field>
          <Field label="سمت" htmlFor="pf-headline" className="sm:col-span-2">
            <Input id="pf-headline" value={headline} onChange={(e) => setHeadline(e.target.value)} placeholder="مثلاً مشاور املاک" />
          </Field>
          <Field label="دربارهٔ من" htmlFor="pf-bio" className="sm:col-span-2">
            <Textarea id="pf-bio" rows={3} value={bio} onChange={(e) => setBio(e.target.value)} />
          </Field>
          <Field label="وب‌سایت" htmlFor="pf-website">
            <Input id="pf-website" dir="ltr" value={website} onChange={(e) => setWebsite(e.target.value)} placeholder="https://" />
          </Field>
          <Field label="اینستاگرام" htmlFor="pf-instagram">
            <Input id="pf-instagram" dir="ltr" value={instagram} onChange={(e) => setInstagram(e.target.value)} placeholder="@handle" />
          </Field>
          <Field label="لینکدین" htmlFor="pf-linkedin" className="sm:col-span-2">
            <Input id="pf-linkedin" dir="ltr" value={linkedin} onChange={(e) => setLinkedin(e.target.value)} placeholder="https://" />
          </Field>
          <div className="sm:col-span-2">
            <Button type="submit" disabled={busy} className="gap-1.5">
              {busy && <Loader2 className="size-4 animate-spin" />}
              ذخیرهٔ مشخصات
            </Button>
          </div>
        </form>
      </Section>
    </Reveal>
  );
}
