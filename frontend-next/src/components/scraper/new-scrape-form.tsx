"use client";

// «اسکرپینگ جدید»: the card that starts everything else in this section —
// from a Divar link, or filled by hand, checked against Divar's own count
// before it runs, and either started once or saved to run itself every day.

import { AlarmClock, Info, Link2, Play } from "lucide-react";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { qs } from "@/lib/crm";
import { Field, NativeSelect, Section } from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import { faNum } from "@/lib/format";
import { AccountPicker, useMyCookies } from "./account-picker";
import { CookieWarningDialog } from "./cookie-warning-dialog";
import { DigitsInput } from "./digits-input";
import { MoreFilters } from "./more-filters";
import { RotationHint } from "./rotation-hint";
import { SaveScheduleDialog } from "./schedule-dialog";
import type { Category, City, EstimateResponse, ParseLinkResponse, ScrapeConfig, ScrapeJob } from "./types";
import { divarUsable } from "./types";
import { filtersToParams, useScrapeForm } from "./use-scrape-form";

export function NewScrapeForm() {
  const qc = useQueryClient();
  const form = useScrapeForm();
  const { state, set } = form;

  const cities = useQuery({ queryKey: ["scraper", "cities"], queryFn: () => api<City[]>("/scraper/cities"), staleTime: 10 * 60_000 });
  const categories = useQuery({ queryKey: ["scraper", "categories"], queryFn: () => api<Category[]>("/scraper/categories"), staleTime: 10 * 60_000 });
  const cookies = useMyCookies();
  const validSessions = (cookies.data?.cookies ?? []).filter((c) => c.is_valid).length;

  const category = categories.data?.find((c) => c.slug === state.category);
  const cityLabel = cities.data?.find((c) => c.slug === state.city)?.name ?? state.city;
  const categoryLabel = category?.name ?? state.category;

  function onCategoryChange(slug: string) {
    const next = categories.data?.find((c) => c.slug === slug);
    set("category", slug);
    // switching deal type leaves the other type's price fields behind
    // unless they are cleared — a stale «۵۰۰ میلیون» from «خرید» would
    // otherwise silently narrow an «اجاره» run.
    if (next?.type === "rent") {
      set("minPrice", "");
      set("maxPrice", "");
      set("minPricePerMeter", "");
      set("maxPricePerMeter", "");
    } else if (next?.type === "buy") {
      set("minDeposit", "");
      set("maxDeposit", "");
      set("minRent", "");
      set("maxRent", "");
    }
  }

  /* ── از روی لینک دیوار ──────────────────────────────────────────── */
  const [link, setLink] = useState("");
  const [linkNote, setLinkNote] = useState<{ tone: "success" | "danger"; text: string; ignored?: string[] } | null>(null);
  const parseLink = useMutation({
    mutationFn: (url: string) => api<ParseLinkResponse>("/scraper/parse-link", { json: { url } }),
    onSuccess: (d) => {
      form.applyParsed(d.city, d.category, d.filters);
      const where = [d.city_name, d.category_name].filter(Boolean).join(" — ");
      setLinkNote({ tone: "success", text: `فرم پر شد — ${where}`, ignored: d.ignored });
      toast.success("انجام شد", `فرم از روی لینک پر شد — ${where}`);
    },
    onError: (e) => setLinkNote({ tone: "danger", text: e instanceof ApiError ? e.message : "این لینک خوانده نشد" }),
  });
  function applyLink() {
    const url = link.trim();
    if (!url) {
      toast.info("توجه", "اول لینک را بچسبانید");
      return;
    }
    setLinkNote(null);
    parseLink.mutate(url);
  }

  /* ── چند آگهی با این فیلترها هست؟ ──────────────────────────────── */
  const [debounced, setDebounced] = useState(state);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(state), 900);
    return () => clearTimeout(t);
  }, [state]);
  const estimateQuery = qs({ city: debounced.city, category: debounced.category || undefined, ...filtersToParams(debounced) });
  const estimate = useQuery({
    queryKey: ["scraper", "estimate", estimateQuery],
    queryFn: () => api<EstimateResponse>(`/scraper/estimate${estimateQuery}`),
    enabled: !!debounced.city,
    staleTime: 20_000,
  });

  /* ── شروع اسکرپینگ ──────────────────────────────────────────────── */
  const [cookieWarning, setCookieWarning] = useState(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);

  const start = useMutation({
    mutationFn: (config: ScrapeConfig) => api<ScrapeJob>("/scraper/start", { json: config }),
    onSuccess: (job) => {
      toast.success("موفق", `اسکرپینگ شروع شد: ${job.job_id}${job.divar_phone ? ` (${job.divar_phone})` : ""}`);
      qc.invalidateQueries({ queryKey: ["scraper", "jobs"] });
    },
    onError: (e) => toast.error("خطا", e instanceof ApiError ? e.message : undefined),
  });

  function accountIsUsable(): { usable: boolean; hasCookies: boolean } {
    const rows = cookies.data?.cookies ?? [];
    if (state.account) {
      const row = rows.find((c) => c.phone_number === state.account);
      return { usable: divarUsable(row), hasCookies: !!row };
    }
    return { usable: rows.some((c) => divarUsable(c)), hasCookies: rows.length > 0 };
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!state.city || !state.category) {
      toast.info("توجه", "اول شهر و دسته‌بندی را انتخاب کنید");
      return;
    }
    const { usable } = accountIsUsable();
    if (!usable) {
      setCookieWarning(true);
      return;
    }
    start.mutate(form.buildConfig());
  }

  const { hasCookies } = accountIsUsable();

  return (
    <Section title="اسکرپینگ جدید" bodyClassName="grid gap-4 p-4">
      {/* از روی لینک دیوار */}
      <div className="grid gap-1.5 rounded-xl border bg-background/40 p-3">
        <label htmlFor="scraper-link" className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
          <Link2 className="size-3.5" aria-hidden /> از روی لینک دیوار
        </label>
        <div className="flex gap-2">
          <Input
            id="scraper-link"
            dir="ltr"
            type="url"
            placeholder="divar.ir/s/urmia/rent-apartment?..."
            value={link}
            onChange={(e) => setLink(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                applyLink();
              }
            }}
          />
          <Button type="button" variant="outline" className="shrink-0" onClick={applyLink} disabled={parseLink.isPending}>
            پر کن
          </Button>
        </div>
        {linkNote && (
          <div className={`text-xs ${linkNote.tone === "success" ? "text-success" : "text-destructive"}`}>
            {linkNote.text}
            {!!linkNote.ignored?.length && <div className="text-warning">این‌ها منتقل نشدند: {linkNote.ignored.join("، ")}</div>}
          </div>
        )}
      </div>

      <form onSubmit={submit} className="grid gap-4">
        <Field label="شهر" htmlFor="scraper-city">
          <NativeSelect id="scraper-city" required value={state.city} onChange={(e) => set("city", e.target.value)}>
            <option value="">انتخاب شهر…</option>
            {(cities.data ?? []).map((c) => (
              <option key={c.slug} value={c.slug}>{c.name}</option>
            ))}
          </NativeSelect>
        </Field>

        <Field label="دسته‌بندی" htmlFor="scraper-category">
          <NativeSelect id="scraper-category" required value={state.category} onChange={(e) => onCategoryChange(e.target.value)}>
            <option value="">انتخاب دسته‌بندی…</option>
            {(categories.data ?? []).map((c) => (
              <option key={c.slug} value={c.slug}>{c.name}</option>
            ))}
          </NativeSelect>
        </Field>

        <AccountPicker value={state.account} onChange={(v) => set("account", v)} rotateEvery={form.toNumber(state.rotateEvery)} />

        <MoreFilters state={state} set={set} categoryType={category?.type} activeCount={form.activeFilterCount} />

        <Field
          label="تعداد آگهی برای اسکرپ"
          htmlFor="scraper-pages"
          hint={state.postedDate ? "خالی بگذارید تا همهٔ آگهی‌های آن تاریخ اسکرپ شوند؛ برای محدود کردن، عدد وارد کنید" : "خالی = پیش‌فرض سرور (۱۰۰ آگهی)"}
        >
          <DigitsInput id="scraper-pages" aria-label="تعداد آگهی برای اسکرپ" value={state.pages} onChange={(v) => set("pages", v)} placeholder="۱۰۰" />
        </Field>

        <Field label="چرخش شماره دیوار (هر چند آگهی)" htmlFor="scraper-rotate-every">
          <DigitsInput id="scraper-rotate-every" aria-label="چرخش شماره دیوار" value={state.rotateEvery} onChange={(v) => set("rotateEvery", v)} placeholder="۱۰۰" />
          <div className="mt-1.5">
            <RotationHint n={form.toNumber(state.rotateEvery)} validSessions={validSessions} />
          </div>
        </Field>

        <label className="flex items-center gap-2 text-sm">
          <Checkbox checked={state.downloadImages} onCheckedChange={(v) => set("downloadImages", v === true)} /> دانلود تصاویر
        </label>

        <div className="rounded-xl border bg-background/40 p-3">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
            <Info className="size-3.5" aria-hidden /> چند آگهی با این فیلترها هست؟
          </div>
          {!state.city ? (
            <p className="mt-1 text-xs text-muted-foreground">اول شهر را انتخاب کنید.</p>
          ) : estimate.isFetching && !estimate.data ? (
            <p className="mt-1 text-xs text-muted-foreground">در حال شمارش…</p>
          ) : estimate.data?.error ? (
            <p className="mt-1 text-xs text-destructive">{estimate.data.error}</p>
          ) : estimate.data?.count != null ? (
            <>
              <p className="mt-1 text-lg font-black tabular">{faNum(estimate.data.count)} آگهی</p>
              {!!estimate.data.applied_after_scrape.length && (
                <p className="mt-1 text-[11px] leading-5 text-warning">
                  این فیلترها را دیوار نمی‌فهمد و فقط بعد از باز کردن هر آگهی اعمال می‌شود، پس نتیجهٔ واقعی ممکن است کمتر از این عدد باشد: {estimate.data.applied_after_scrape.join("، ")}
                </p>
              )}
            </>
          ) : (
            <p className="mt-1 text-xs text-muted-foreground">—</p>
          )}
        </div>

        {!hasCookies && state.city && (
          <p className="text-xs text-warning">هنوز هیچ شمارهٔ دیواری برای حساب شما ثبت نشده — بدون ورود، شمارهٔ تماس آگهی‌ها گرفته نمی‌شود.</p>
        )}

        <Button type="submit" className="w-full shadow-[0_8px_24px_-8px_rgb(99_102_241/0.8)]" disabled={start.isPending}>
          <Play /> شروع اسکرپینگ
        </Button>
        <Button
          type="button"
          variant="outline"
          className="w-full"
          onClick={() => setScheduleOpen(true)}
          disabled={!state.city || !state.category}
          title="همین فیلترها، هر روز در ساعتی که می‌گویید"
        >
          <AlarmClock /> هر روز خودکار اجرا شود
        </Button>
      </form>

      <CookieWarningDialog
        open={cookieWarning}
        onOpenChange={setCookieWarning}
        expired={hasCookies}
        onContinue={() => start.mutate(form.buildConfig())}
      />
      <SaveScheduleDialog open={scheduleOpen} onOpenChange={setScheduleOpen} config={state.city && state.category ? form.buildConfig() : null} cityLabel={cityLabel} categoryLabel={categoryLabel} />
    </Section>
  );
}
