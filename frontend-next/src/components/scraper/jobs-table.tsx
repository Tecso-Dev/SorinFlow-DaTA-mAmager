"use client";

// «تسک‌های اسکرپینگ»: every run, live. Polls every 5s through TanStack
// Query's own refetchInterval — scoped to this component's query observer,
// so it starts when the table mounts and stops the moment it unmounts, with
// no timer left running anywhere else.

import {
  ArrowLeftRight, Ban, ListFilter, MoreVertical, Play, RefreshCw, RotateCcw, ScrollText, SatelliteDish, Trash2,
} from "lucide-react";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import {
  Empty, ErrorNote, ListSkeleton, NativeSelect, Section, Toolbar, ToneBadge, useConfirm,
} from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, ApiError } from "@/lib/api";
import { faDate, faNum } from "@/lib/format";
import { useSession } from "@/lib/session";
import { useMyCookies } from "./account-picker";
import { JobLogDialog } from "./job-log-dialog";
import { ProgressRing } from "./progress-ring";
import { ScraperLogDialog } from "./scraper-log-dialog";
import { SkippedDialog } from "./skipped-dialog";
import { SwitchAccountDialog } from "./switch-account-dialog";
import { JOB_STATUS_FA, JOB_STATUS_TONE, type Category, type JobList, type JobStatus, type ScrapeJob } from "./types";

const LIVE: JobStatus[] = ["running", "paused", "pending"];
const FINISHED: JobStatus[] = ["completed", "failed", "cancelled"];
const FULL_ACCESS = new Set(["root", "super_admin"]);

function when(iso: string | null): string {
  if (!iso) return "—";
  return faDate(new Date(iso), { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function JobsTable({ categories }: { categories: Category[] }) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const session = useSession().data?.user;
  const cookies = useMyCookies();
  const [category, setCategory] = useState("");
  const [logJob, setLogJob] = useState<string | null>(null);
  const [skippedJob, setSkippedJob] = useState<ScrapeJob | null>(null);
  const [switchJob, setSwitchJob] = useState<ScrapeJob | null>(null);
  const [scraperLog, setScraperLog] = useState(false);

  const jobs = useQuery({
    queryKey: ["scraper", "jobs", category],
    queryFn: () => api<JobList>(`/scraper/jobs?limit=20${category ? `&category=${encodeURIComponent(category)}` : ""}`),
    refetchInterval: 5000,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["scraper", "jobs"] });

  const cancel = useMutation({
    mutationFn: (jobId: string) => api<{ message: string; was: string; otp_cleared: boolean }>(`/scraper/jobs/${jobId}/cancel`, { method: "POST" }),
    onSuccess: (r) => {
      toast.success("لغو شد", r.otp_cleared ? "درخواست کد پیامکی این اسکرپ هم پاک شد" : undefined);
      invalidate();
    },
    onError: (e) => toast.error("لغو نشد", e instanceof ApiError ? e.message : undefined),
  });

  const resume = useMutation({
    mutationFn: (jobId: string) => api<ScrapeJob>(`/scraper/jobs/${jobId}/resume`, { method: "POST" }),
    onSuccess: (job) => {
      toast.success("ادامه شروع شد", `تسک تازه: ${job.job_id}`);
      invalidate();
    },
    onError: (e) => toast.error("ادامه ممکن نشد", e instanceof ApiError ? e.message : undefined),
  });

  const del = useMutation({
    mutationFn: (jobId: string) => api(`/scraper/jobs/${jobId}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("حذف شد");
      invalidate();
    },
    onError: (e) => toast.error("حذف نشد", e instanceof ApiError ? e.message : undefined),
  });

  async function onCancel(job: ScrapeJob) {
    const ok = await confirm({ title: "لغو اسکرپ", description: "این تسک لغو شود؟", confirm: "لغو تسک", danger: true, icon: Ban });
    if (ok) cancel.mutate(job.job_id);
  }
  async function onDelete(job: ScrapeJob) {
    const ok = await confirm({
      title: "حذف تسک",
      description: "گزارش و فهرست آگهی‌های ردشدهٔ این تسک حذف می‌شود؛ آگهی‌های ذخیره‌شده دست‌نخورده می‌مانند.",
      confirm: "حذف",
      danger: true,
      icon: Trash2,
    });
    if (ok) del.mutate(job.job_id);
  }

  const sessionValid = (cookies.data?.cookies ?? []).some((c) => c.is_valid && c.is_enabled !== false);
  const statsAllowed = !!session?.permissions.includes("stats") || (session ? FULL_ACCESS.has(session.role) : false);
  const items = jobs.data?.items ?? [];

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          تسک‌های اسکرپینگ
          <span
            className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold", sessionValid ? "bg-success/12 text-success" : "bg-warning/15 text-warning")}
            title={sessionValid ? "نشست دیوار فعال" : "نشست دیوار غیرفعال — شماره تماس اسکرپ نمی‌شود"}
          >
            <SatelliteDish className="size-3" aria-hidden />
            {cookies.isLoading ? "…" : sessionValid ? "فعال" : "غیرفعال"}
          </span>
        </span>
      }
      action={
        <Toolbar>
          <NativeSelect aria-label="فیلتر دسته‌بندی" className="w-auto min-w-40" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">همهٔ دسته‌بندی‌ها</option>
            {categories.map((c) => <option key={c.slug} value={c.name}>{c.name}</option>)}
          </NativeSelect>
          {statsAllowed && (
            <Button variant="outline" size="sm" onClick={() => setScraperLog(true)} title="ببینید اسکرپر روی هر آگهی چه تصمیمی گرفته">
              <ListFilter /> لاگ اسکرپر
            </Button>
          )}
          <Button variant="ghost" size="icon-sm" onClick={() => jobs.refetch()} aria-label="بروزرسانی" disabled={jobs.isFetching}>
            <RefreshCw className={cn(jobs.isFetching && "animate-spin")} />
          </Button>
        </Toolbar>
      }
      bodyClassName="p-0 pt-0"
      className="overflow-hidden"
    >
      {jobs.isLoading ? (
        <div className="p-5"><ListSkeleton rows={6} /></div>
      ) : jobs.isError ? (
        <div className="p-5"><ErrorNote error={jobs.error} /></div>
      ) : items.length === 0 ? (
        <div className="p-5"><Empty icon={SatelliteDish}>هیچ تسکی وجود ندارد.</Empty></div>
      ) : (
        <div className="overflow-x-auto">
          <Table className="text-[13px]">
            <TableHeader>
              <TableRow className="bg-muted/40 hover:bg-muted/40">
                <TableHead>#</TableHead>
                <TableHead>دسته‌بندی</TableHead>
                <TableHead>شهر</TableHead>
                <TableHead title="چه کسی اسکرپ را شروع کرد، و با کدام حساب دیوار">کاربر / حساب</TableHead>
                <TableHead>وضعیت</TableHead>
                <TableHead>پیشرفت</TableHead>
                <TableHead title="بررسی‌شده / کل">بررسی / کل</TableHead>
                <TableHead>جدید / بروز</TableHead>
                <TableHead>شروع</TableHead>
                <TableHead className="w-10"><span className="sr-only">عملیات</span></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((job) => {
                const mine = job.owner_user_id != null && job.owner_user_id === session?.id;
                const canSwitch = mine && LIVE.includes(job.status);
                const canCancel = LIVE.includes(job.status) && (mine || (session ? FULL_ACCESS.has(session.role) : false));
                const canDelete = FINISHED.includes(job.status) && (job.owner_user_id == null || mine || (session ? FULL_ACCESS.has(session.role) : false));
                return (
                  <TableRow key={job.id}>
                    <TableCell className="font-mono text-xs text-muted-foreground" title={job.job_id}>{job.job_id.slice(0, 6)}</TableCell>
                    <TableCell>{job.category_name ? <ToneBadge tone="primary">{job.category_name}</ToneBadge> : "—"}</TableCell>
                    <TableCell>{job.city_name || "—"}</TableCell>
                    <TableCell className="whitespace-nowrap">
                      <div>{job.owner_name || "—"}</div>
                      {job.divar_phone ? (
                        <div dir="ltr" className="text-[11px] text-muted-foreground" title={job.accounts_used.length > 1 ? `حساب‌ها به ترتیب: ${job.accounts_used.join("، ")}` : "حساب دیوار این اجرا"}>
                          {job.divar_phone}
                          {job.accounts_used.length > 1 && <span className="ms-1">+{faNum(job.accounts_used.length - 1)}</span>}
                        </div>
                      ) : (
                        <div className="text-[11px] text-muted-foreground">{job.status === "pending" ? "خودکار" : "—"}</div>
                      )}
                    </TableCell>
                    <TableCell>
                      <ToneBadge tone={JOB_STATUS_TONE[job.status]}>{JOB_STATUS_FA[job.status]}</ToneBadge>
                      {job.resumed_from && (
                        <div className="mt-0.5 text-[11px] text-muted-foreground" title="این اجرا ادامهٔ اجرای قبلی است">
                          <RotateCcw className="me-0.5 inline size-3" aria-hidden />ادامهٔ {job.resumed_from.slice(0, 8)}
                        </div>
                      )}
                      {job.finish_reason && <div className="mt-0.5 max-w-40 truncate text-[11px] text-muted-foreground" title={job.finish_reason}>{job.finish_reason}</div>}
                    </TableCell>
                    <TableCell>
                      <ProgressRing value={job.progress} />
                    </TableCell>
                    <TableCell className="text-center tabular" title={job.divar_count ? "کل = تعدادی که دیوار برای این فیلترها اعلام کرد" : "کل = نامزدهای جمع‌شده"}>
                      {job.total_items ? <>{faNum(job.scraped_items)} <span className="text-muted-foreground">/</span> {faNum(job.total_items)}</> : <span className="text-muted-foreground">—</span>}
                    </TableCell>
                    <TableCell className="text-center tabular">
                      <span className="text-success" title="ردیف تازه">{faNum(job.new_items)}</span>{" "}
                      <span className="text-muted-foreground">/</span>{" "}
                      <span className="text-muted-foreground" title="از قبل موجود بود">{faNum(job.updated_items)}</span>
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-[12px]">{when(job.started_at)}</TableCell>
                    <TableCell>
                      <DropdownMenu dir="rtl">
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="icon-sm" aria-label="کارهای بیشتر"><MoreVertical /></Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" className="w-56">
                          <DropdownMenuItem onSelect={() => setLogJob(job.job_id)}><ScrollText /> گزارش این اسکرپ</DropdownMenuItem>
                          <DropdownMenuItem onSelect={() => setSkippedJob(job)}><ListFilter /> آگهی‌های ردشده</DropdownMenuItem>
                          {canSwitch && <DropdownMenuItem onSelect={() => setSwitchJob(job)}><ArrowLeftRight /> تعویض شماره</DropdownMenuItem>}
                          {job.can_resume && (
                            <DropdownMenuItem onSelect={() => resume.mutate(job.job_id)}><Play /> ادامه</DropdownMenuItem>
                          )}
                          {canCancel && (
                            <DropdownMenuItem variant="destructive" onSelect={() => onCancel(job)}><Ban /> لغو</DropdownMenuItem>
                          )}
                          {canDelete && (
                            <DropdownMenuItem variant="destructive" onSelect={() => onDelete(job)}><Trash2 /> حذف</DropdownMenuItem>
                          )}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}

      <JobLogDialog jobId={logJob} onClose={() => setLogJob(null)} />
      <SkippedDialog job={skippedJob} onClose={() => setSkippedJob(null)} />
      <SwitchAccountDialog job={switchJob} onClose={() => setSwitchJob(null)} />
      <ScraperLogDialog open={scraperLog} onOpenChange={setScraperLog} />
    </Section>
  );
}
