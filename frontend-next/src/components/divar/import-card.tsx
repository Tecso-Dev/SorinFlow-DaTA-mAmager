"use client";

// «افزودن نشست دستی (Import کوکی)»: paste a cookie jar exported from a
// browser extension. The server actually asks Divar whether it works and
// answers with a real alive: true|false|null — never just «ذخیره شد».

import { FolderInput } from "lucide-react";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Field, Section } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import { parseDigits } from "@/lib/format";

const PHONE_RE = /^09\d{9}$/;
const PLACEHOLDER = '[{"name":"token","value":"...","domain":".divar.ir"}]';

export function ImportCard() {
  const qc = useQueryClient();
  const [phone, setPhone] = useState("");
  const [raw, setRaw] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const p = parseDigits(phone).trim();
    if (!PHONE_RE.test(p)) {
      setError("شمارهٔ موبایل معتبر وارد کنید (مثل ۰۹۱۲۳۴۵۶۷۸۹)");
      return;
    }
    let cookies: unknown;
    try {
      cookies = JSON.parse(raw.trim());
      if (!Array.isArray(cookies)) throw new Error("باید یک آرایه باشد");
    } catch (err) {
      setError(`فرمت JSON نادرست است: ${err instanceof Error ? err.message : String(err)}`);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const r = await api<{ success: boolean; alive: boolean | null; message: string; expires_at: string | null }>(
        "/auth/cookies/import",
        { json: { phone_number: p, cookies } },
      );
      if (r.alive === true) {
        toast.success("تأیید شد", r.message);
        setRaw("");
      } else if (r.alive === false) {
        toast.error("رد شد", r.message);
      } else {
        toast.info("نامشخص", r.message);
      }
      qc.invalidateQueries({ queryKey: ["divar", "cookies"] });
      qc.invalidateQueries({ queryKey: ["divar", "registry"] });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "افزودن نشست ناموفق بود");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title="افزودن نشست دستی" hint="کوکی‌های کپی‌شده از افزونهٔ مرورگر (مثل EditThisCookie) را وارد کنید">
      <form onSubmit={submit} className="grid gap-3">
        <Field label="شمارهٔ موبایل دیوار" htmlFor="import-phone">
          <Input id="import-phone" dir="ltr" inputMode="tel" placeholder="09123456789" value={phone} onChange={(e) => setPhone(e.target.value)} />
        </Field>
        <Field label="کوکی‌ها (JSON)" htmlFor="import-json" error={error || undefined} hint="یک آرایه از اشیاء کوکی، شامل کوکی نشست دیوار">
          <Textarea
            id="import-json"
            dir="ltr"
            rows={5}
            placeholder={PLACEHOLDER}
            className="font-mono text-xs"
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
          />
        </Field>
        <Button type="submit" disabled={busy} className="w-fit">
          <FolderInput /> وارد کردن نشست
        </Button>
      </form>
    </Section>
  );
}
