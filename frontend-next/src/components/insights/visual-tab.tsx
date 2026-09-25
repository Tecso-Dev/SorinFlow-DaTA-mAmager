"use client";

// «تحلیل تصویری و قیمت» — GET /properties/visual/overview: three things
// measured from what the scraper actually holds (photos, prices), never a
// model this deployment cannot run. Everything that cannot be judged answers
// «—», not a fake number — see insights/format.ts.

import { AlertTriangle, Copy, Gauge, Loader2, Play, ShieldAlert, TrendingDown } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CountUp, Reveal, Tilt } from "@/components/viz";
import { Empty, ErrorNote, ListSkeleton, Section, ToneBadge } from "@/components/panel/kit";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { faNum } from "@/lib/format";
import { can, useSession } from "@/lib/session";
import { insNum, insToman } from "./format";
import type { PhotoStatus } from "../properties/types";
import type { VisualOverview } from "./types";

const PHOTO_PROBLEM_FA: Record<string, string> = { blurry: "تار", exposure: "نور نامناسب", small: "رزولوشن پایین" };
const STAT_TINT = ["text-chart-1 bg-chart-1/12", "text-chart-4 bg-chart-4/12", "text-warning bg-warning/12", "text-chart-2 bg-chart-2/12"];

function StatCard({ icon: Icon, label, value, tint, delay = 0 }: { icon: React.ComponentType<{ className?: string }>; label: string; value: number | null; tint: string; delay?: number }) {
  return (
    <Reveal delay={delay}>
      <Tilt className="flex h-full flex-col gap-3 rounded-2xl border bg-card p-4 shadow-sm dark:bg-linear-to-b dark:from-white/[0.04] dark:to-transparent dark:shadow-none">
        <div className={`grid size-10 shrink-0 place-items-center rounded-xl ring-1 ring-inset ring-current/20 shadow-[0_0_20px_-6px_currentColor] ${tint}`}>
          <Icon className="size-5" />
        </div>
        <div>
          <div className="text-[13px] text-muted-foreground">{label}</div>
          <div className="mt-1 text-[26px] leading-none font-black tracking-tight tabular">
            {value === null ? <span className="text-muted-foreground">—</span> : <CountUp value={value} />}
          </div>
        </div>
      </Tilt>
    </Reveal>
  );
}

function serial(n: number | null) {
  return n === null ? "—" : faNum(n, { useGrouping: false });
}

export function VisualTab() {
  const user = useSession().data?.user;
  const qc = useQueryClient();
  const isSuper = can(user, { roles: ["root", "super_admin"] });
  const q = useQuery({ queryKey: ["properties", "visual"], queryFn: () => api<VisualOverview>("/properties/visual/overview?limit=25") });

  const status = useQuery({
    queryKey: ["ai", "photo", "status"],
    queryFn: () => api<PhotoStatus>("/ai/photo/status"),
    enabled: isSuper,
    retry: false,
  });
  const run = useMutation({
    mutationFn: () => api<{ tagged: number; skipped: number; failed: number; stopped: string | null }>("/ai/photo/run?limit=30", { method: "POST" }),
    onSuccess: (r) => {
      const STOP_FA: Record<string, string> = { BudgetExceeded: "سقف بودجه پر شد", Disabled: "از پنل خاموش است", NotConfigured: "تنظیم نشده" };
      if (r.stopped) toast.info("یک دور هوش تصویری", STOP_FA[r.stopped] ?? r.stopped);
      else toast.success("یک دور هوش تصویری", `${faNum(r.tagged)} برچسب خورد، ${faNum(r.skipped)} بدون عکس، ${faNum(r.failed)} ناموفق`);
      qc.invalidateQueries({ queryKey: ["ai", "photo", "status"] });
    },
    onError: (e) => toast.error("اجرا نشد", e instanceof ApiError ? e.message : undefined),
  });

  if (q.isLoading) return <ListSkeleton rows={6} />;
  if (q.isError) return <ErrorNote error={q.error} />;
  const d = q.data;
  if (!d) return null;

  const { valuation, duplicates, photos } = d;
  const problemsLine = Object.entries(photos.problems).length
    ? Object.entries(photos.problems).map(([k, n]) => `${PHOTO_PROBLEM_FA[k] ?? k}: ${faNum(n)}`).join(" · ")
    : `${faNum(photos.scored_listings)} آگهی سنجیده شد`;

  const statusLine = !status.data
    ? null
    : !status.data.configured
      ? "هوش مصنوعی تنظیم نشده"
      : !status.data.enabled
        ? "از پنل خاموش است"
        : "فعال — هر ۵ دقیقه یک دور";

  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
        <StatCard icon={TrendingDown} label="زیر قیمت منطقه" value={valuation.under.length} tint={STAT_TINT[0]} />
        <StatCard icon={Copy} label="ملک تکراری" value={duplicates.total_pairs} tint={STAT_TINT[1]} delay={0.04} />
        <StatCard icon={ShieldAlert} label="آگهی با عکس ضعیف" value={photos.weak.length} tint={STAT_TINT[2]} delay={0.08} />
        <StatCard icon={Gauge} label="قابل ارزش‌گذاری" value={valuation.judged} tint={STAT_TINT[3]} delay={0.12} />
      </div>

      <Reveal delay={0.1}>
        <p className="rounded-xl border bg-muted/30 px-4 py-2.5 text-sm leading-7 text-muted-foreground">
          از {faNum(valuation.total)} ملک، <b className="text-foreground">{faNum(valuation.judged)}</b> قابل ارزش‌گذاری بود — بقیه یا متراژ ندارند یا محله‌شان هنوز به {faNum(valuation.min_comparables)} آگهی مشابه نرسیده. {faNum(valuation.districts_with_a_benchmark)} محله از {faNum(valuation.districts_seen)} محله پایهٔ قیمت دارد.
        </p>
      </Reveal>

      <Reveal delay={0.14}>
        <Section title="زیر قیمت منطقه" hint="نسبت به میانهٔ قیمت هر متر در همان محله">
          {!valuation.under.length ? (
            <Empty icon={TrendingDown}>هیچ ملکی به‌اندازهٔ قابل توجه زیر میانهٔ محله‌اش نیست.</Empty>
          ) : (
            <div className="overflow-x-auto rounded-xl border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>کد</TableHead><TableHead>عنوان</TableHead><TableHead>محله</TableHead>
                    <TableHead>متراژ</TableHead><TableHead>قیمت هر متر</TableHead><TableHead>اختلاف</TableHead><TableHead>پایه</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {valuation.under.map((it) => (
                    <TableRow key={it.id}>
                      <TableCell className="font-mono text-xs tabular text-primary">{serial(it.serial_no)}</TableCell>
                      <TableCell className="max-w-64 truncate" title={it.title}>{it.title}</TableCell>
                      <TableCell>{it.district || "—"}</TableCell>
                      <TableCell className="tabular">{it.area ? `${faNum(it.area)} متر` : "—"}</TableCell>
                      <TableCell className="tabular">{insToman(it.ppm)}</TableCell>
                      <TableCell><ToneBadge tone="success">{faNum(Math.abs(it.delta_pct))}٪</ToneBadge></TableCell>
                      <TableCell className="tabular text-xs">
                        {faNum(it.sample)}{it.confidence === "thin" && <span title="نمونهٔ کم" className="ms-1 text-warning">⚠</span>}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </Section>
      </Reveal>

      <Reveal delay={0.18}>
        <Section title="یک ملک، چند آگهی" hint={`${faNum(duplicates.with_hashes)} آگهی عکس اثرانگشت‌شده دارد · ${faNum(duplicates.boilerplate_ignored)} عکس تکراری (لوگو/نما) نادیده گرفته شد`}>
          {!duplicates.pairs.length ? (
            <Empty icon={Copy}>ملک تکراری پیدا نشد. عکس‌ها از اسکرپ بعدی اثرانگشت می‌گیرند.</Empty>
          ) : (
            <div className="overflow-x-auto rounded-xl border">
              <Table>
                <TableHeader>
                  <TableRow><TableHead>تشخیص</TableHead><TableHead>آگهی اول</TableHead><TableHead>آگهی دوم</TableHead><TableHead>اختلاف قیمت</TableHead></TableRow>
                </TableHeader>
                <TableBody>
                  {duplicates.pairs.map((pair, i) => {
                    const gap = pair.a.price && pair.b.price ? Math.abs(pair.a.price - pair.b.price) : null;
                    return (
                      <TableRow key={i}>
                        <TableCell>
                          <ToneBadge tone={pair.verdict === "duplicate" ? "danger" : "warning"}>{pair.note}</ToneBadge>
                        </TableCell>
                        <TableCell className="text-xs">
                          <div className="font-mono tabular text-primary">{serial(pair.a.serial_no)}</div>
                          <div className="truncate max-w-48" title={pair.a.title}>{pair.a.title}</div>
                          <div className="text-muted-foreground">{pair.a.seller || "—"}</div>
                        </TableCell>
                        <TableCell className="text-xs">
                          <div className="font-mono tabular text-primary">{serial(pair.b.serial_no)}</div>
                          <div className="truncate max-w-48" title={pair.b.title}>{pair.b.title}</div>
                          <div className="text-muted-foreground">{pair.b.seller || "—"}</div>
                        </TableCell>
                        <TableCell className="tabular text-xs">
                          {gap === null ? "—" : gap === 0 ? "هر دو یک قیمت" : insToman(gap)}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </Section>
      </Reveal>

      <Reveal delay={0.22}>
        <Section title="کیفیت عکس‌ها" hint={problemsLine}>
          {!photos.weak.length ? (
            <Empty icon={ShieldAlert}>عکس ضعیفی پیدا نشد. کیفیت عکس‌ها از اسکرپ بعدی سنجیده می‌شود.</Empty>
          ) : (
            <div className="overflow-x-auto rounded-xl border">
              <Table>
                <TableHeader>
                  <TableRow><TableHead>کد</TableHead><TableHead>عنوان</TableHead><TableHead>تعداد عکس</TableHead><TableHead>بدترین</TableHead><TableHead>ایرادها</TableHead></TableRow>
                </TableHeader>
                <TableBody>
                  {photos.weak.map((w) => (
                    <TableRow key={w.id}>
                      <TableCell className="font-mono text-xs tabular text-primary">{serial(w.serial_no)}</TableCell>
                      <TableCell className="max-w-72 truncate" title={w.title}>{w.title}</TableCell>
                      <TableCell className="tabular">{insNum(w.count)}</TableCell>
                      <TableCell className="tabular">{insNum(w.worst)}</TableCell>
                      <TableCell>
                        {w.problems.length ? (
                          <div className="flex flex-wrap gap-1">
                            {w.problems.map((p) => <ToneBadge key={p} tone="warning">{PHOTO_PROBLEM_FA[p] ?? p}</ToneBadge>)}
                          </div>
                        ) : "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </Section>
      </Reveal>

      {isSuper && (
        <Reveal delay={0.26}>
          <Section title="برچسب‌های هوش تصویری">
            <p className="mb-3 text-xs text-foreground/70">نمونه‌گیری از سه عکس اول هر آگهی</p>
            {status.isLoading ? (
              <ListSkeleton rows={2} />
            ) : status.isError || !status.data ? (
              <p className="text-sm text-muted-foreground">در دسترس نیست.</p>
            ) : (
              <div className="flex flex-col gap-3">
                <div className="grid grid-cols-3 gap-3 text-center">
                  <div><div className="text-xl font-black tabular"><CountUp value={status.data.tagged} /></div><div className="text-xs text-foreground/70">برچسب‌خورده</div></div>
                  <div><div className="text-xl font-black tabular"><CountUp value={status.data.skipped} /></div><div className="text-xs text-foreground/70">بدون عکس</div></div>
                  <div><div className="text-xl font-black tabular"><CountUp value={status.data.behind} /></div><div className="text-xs text-foreground/70">در صف</div></div>
                </div>
                <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-3">
                  <div className="text-xs text-muted-foreground">
                    {statusLine} {status.data.model && `· ${status.data.model}`} · نسخهٔ {faNum(status.data.version)}
                  </div>
                  <Button size="sm" disabled={!status.data.configured || !status.data.enabled || run.isPending} onClick={() => run.mutate()}>
                    {run.isPending ? <Loader2 className="animate-spin" /> : <Play />} یک دور الان
                  </Button>
                </div>
              </div>
            )}
          </Section>
        </Reveal>
      )}

      <Reveal delay={0.3}>
        <Section title="این صفحه چه چیزی را نمی‌سنجد">
          <div className="flex gap-3 text-sm leading-7 text-muted-foreground">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
            <p>
              این صفحه سبک آشپزخانه، جنس کف‌پوش، برند لوازم، نقشهٔ ساختمان یا ویرایش تصویر (مثل دکوراسیون مجازی یا تغییر نور) را تشخیص نمی‌دهد.
              «برچسب‌های هوش تصویری» بالا برآمده از یک مدل بینایی روی سه عکس اول هر آگهی است؛ باقی این صفحه از روی پیکسل‌ها و قیمت‌های واقعی اندازه‌گیری می‌شود، نه حدس زده.
            </p>
          </div>
        </Section>
      </Reveal>
    </div>
  );
}
