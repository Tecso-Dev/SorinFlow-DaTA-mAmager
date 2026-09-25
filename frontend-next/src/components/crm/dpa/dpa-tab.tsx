"use client";

// ارزیابی روزانه (DPA) — list with search/date filter, the scored form, delete
// and Excel export. The score is computed on the client EXACTLY like the old
// panel's updateDpaScore (frontend/js/app.js), against the weights in
// app/models/crm_models.py DailyPerformance (BASE_TASKS/ACTIVITIES/BONUS_POINTS/PENALTY_POINTS).

import { ClipboardCheck, Download, Loader2, PenLine, Plus, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/components/toaster";
import { Reveal } from "@/components/viz";
import { JalaliDateInput } from "@/components/panel/date-input";
import {
  Empty, ErrorNote, Field, ListSkeleton, NativeSelect, PageHeader, RingDialog, Section, ToneBadge, Toolbar,
  useConfirm,
} from "@/components/panel/kit";
import { api, ApiError } from "@/lib/api";
import { exportHref, qs } from "@/lib/crm";
import { formatJalali, parseJalali } from "@/lib/jalali";
import { faNum } from "@/lib/format";

/* ───────────────────────── weights — mirrors DailyPerformance exactly ───────────────────────── */

type BaseTask = { key: string; weight: number; time: string; label: string; rest?: boolean };

const BASE_TASKS: BaseTask[] = [
  { key: "prospecting", weight: 25, time: "۰۹:۰۰ – ۱۰:۳۰", label: "شکار فایل — تماس سرد و ثبت لید جدید" },
  { key: "divar_seo", weight: 15, time: "۱۰:۳۰ – ۱۲:۰۰", label: "هک الگوریتم دیوار — بازتولید آگهی و سئو محلی" },
  { key: "followup", weight: 20, time: "۱۲:۰۰ – ۱۴:۰۰", label: "حلقه پیگیری — تماس با مشتریان در حال مذاکره" },
  { key: "__rest", weight: 0, time: "۱۴:۰۰ – ۱۶:۰۰", label: "استراحت — بازیابی انرژی", rest: true },
  { key: "showings", weight: 30, time: "۱۶:۰۰ – ۱۹:۰۰", label: "بازدید حضوری — پرزنت ملک و مدیریت اعتراضات" },
  { key: "crm_report", weight: 10, time: "۱۹:۰۰ – ۲۰:۰۰", label: "گزارش CRM — ثبت دقیق نتایج" },
];

const ACTIVITY_DEFS_FALLBACK = [
  { key: "call", points: 2, label: "تماس با مشتری", auto: true },
  { key: "showing", points: 10, label: "پرزنت / بازدید ملک", auto: true },
  { key: "new_file", points: 15, label: "ثبت فایل جدید", auto: true },
  { key: "meeting", points: 20, label: "نشست و تنظیم قرارداد", auto: true },
  { key: "exclusive", points: 30, label: "ثبت فایل انحصاری", auto: false },
  { key: "offer", points: 20, label: "دریافت آفر کتبی و بیعانه", auto: false },
  { key: "close", points: 50, label: "بستن قرارداد نهایی", auto: false },
];

const BONUS_POINTS = { exclusive: 30, offer: 20, close: 50 };
const PENALTY_POINTS = { crm_delay: 10, cancel: 15, hot_lead: 20 };
const ROLE_LABEL: Record<string, string> = { hunter: "Hunter", closer: "Closer" };

type ActivityDef = { key: string; points: number; label: string; auto: boolean };

type Dpa = {
  id: number;
  agent_name: string;
  role: string;
  date_jalali: string | null;
  target_points: number;
  new_files: number;
  showings_count: number;
  offers_count: number;
  closed_count: number;
  base_tasks: Record<string, boolean>;
  auto_activities: Record<string, number>;
  activities: Record<string, number>;
  activity_defs: ActivityDef[];
  bonus_exclusive: number;
  bonus_offer: number;
  bonus_close: number;
  pen_crm_delay: number;
  pen_cancel: number;
  pen_hot_lead: number;
  mentor_feedback: string | null;
  rca: string | null;
  base_score: number;
  activity_score: number;
  bonus_score: number;
  penalty_score: number;
  total_score: number;
};

const QKEY = ["crm", "dpa"] as const;

/* ───────────────────────── score, computed live — mirrors _dpaScoreParts ───────────────────────── */

function useDpaScore(state: {
  base_tasks: Record<string, boolean>;
  manual: Record<string, number>;
  auto: Record<string, number>;
  activityDefs: ActivityDef[];
  bonus_exclusive: number; bonus_offer: number; bonus_close: number;
  pen_crm_delay: number; pen_cancel: number; pen_hot_lead: number;
}) {
  return useMemo(() => {
    const base = BASE_TASKS.filter((t) => !t.rest && state.base_tasks[t.key]).reduce((a, t) => a + t.weight, 0);
    const activity = state.activityDefs.reduce((sum, a) => {
      const total = (state.auto[a.key] || 0) + (state.manual[a.key] || 0);
      return sum + total * a.points;
    }, 0);
    const bonus = state.bonus_exclusive * BONUS_POINTS.exclusive + state.bonus_offer * BONUS_POINTS.offer + state.bonus_close * BONUS_POINTS.close;
    const penalty = state.pen_crm_delay * PENALTY_POINTS.crm_delay + state.pen_cancel * PENALTY_POINTS.cancel + state.pen_hot_lead * PENALTY_POINTS.hot_lead;
    return { base, activity, bonus, penalty, total: base + activity + bonus - penalty };
  }, [state]);
}

/* ───────────────────────── the scored form ───────────────────────── */

function num(v: string) {
  const n = Number(v);
  return Number.isFinite(n) && n >= 0 ? Math.trunc(n) : 0;
}

function CounterInput({
  id, value, onChange, "aria-label": ariaLabel,
}: { id?: string; value: number; onChange: (n: number) => void; "aria-label"?: string }) {
  return (
    <Input
      id={id} type="number" min={0} dir="ltr" inputMode="numeric" className="w-20 text-center tabular"
      value={value} onChange={(e) => onChange(num(e.target.value))} aria-label={ariaLabel}
    />
  );
}

function DpaDialog({ open, onOpenChange, dpa }: { open: boolean; onOpenChange: (o: boolean) => void; dpa: Dpa | null }) {
  const qc = useQueryClient();
  const [agentName, setAgentName] = useState(dpa?.agent_name ?? "");
  const [role, setRole] = useState(dpa?.role ?? "hunter");
  const [dateJalali, setDateJalali] = useState<Date | null>(() => (dpa?.date_jalali ? parseJalali(dpa.date_jalali) : new Date()));
  const [target, setTarget] = useState(dpa?.target_points ?? 100);
  const [newFiles, setNewFiles] = useState(dpa?.new_files ?? 0);
  const [showingsCount, setShowingsCount] = useState(dpa?.showings_count ?? 0);
  const [offersCount, setOffersCount] = useState(dpa?.offers_count ?? 0);
  const [closedCount, setClosedCount] = useState(dpa?.closed_count ?? 0);
  const [baseTasks, setBaseTasks] = useState<Record<string, boolean>>(dpa?.base_tasks ?? {});
  const [manual, setManual] = useState<Record<string, number>>(() => {
    const defs = dpa?.activity_defs?.length ? dpa.activity_defs : ACTIVITY_DEFS_FALLBACK;
    return Object.fromEntries(defs.map((d) => [d.key, dpa?.activities?.[d.key] ?? 0]));
  });
  const auto = dpa?.auto_activities ?? {};
  const activityDefs = dpa?.activity_defs?.length ? dpa.activity_defs : ACTIVITY_DEFS_FALLBACK;
  const [bonusExclusive, setBonusExclusive] = useState(dpa?.bonus_exclusive ?? 0);
  const [bonusOffer, setBonusOffer] = useState(dpa?.bonus_offer ?? 0);
  const [bonusClose, setBonusClose] = useState(dpa?.bonus_close ?? 0);
  const [penCrmDelay, setPenCrmDelay] = useState(dpa?.pen_crm_delay ?? 0);
  const [penCancel, setPenCancel] = useState(dpa?.pen_cancel ?? 0);
  const [penHotLead, setPenHotLead] = useState(dpa?.pen_hot_lead ?? 0);
  const [rca, setRca] = useState(dpa?.rca ?? "");
  const [mentorFeedback, setMentorFeedback] = useState(dpa?.mentor_feedback ?? "");
  const [busy, setBusy] = useState(false);

  const score = useDpaScore({
    base_tasks: baseTasks, manual, auto, activityDefs,
    bonus_exclusive: bonusExclusive, bonus_offer: bonusOffer, bonus_close: bonusClose,
    pen_crm_delay: penCrmDelay, pen_cancel: penCancel, pen_hot_lead: penHotLead,
  });

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!agentName.trim()) {
      toast.error("نام مشاور الزامی است");
      return;
    }
    setBusy(true);
    try {
      const payload = {
        agent_name: agentName.trim(),
        role,
        date_jalali: dateJalali ? formatJalali(dateJalali) : null,
        target_points: target || 100,
        new_files: newFiles,
        showings_count: showingsCount,
        offers_count: offersCount,
        closed_count: closedCount,
        base_tasks: baseTasks,
        activities: manual,
        bonus_exclusive: bonusExclusive,
        bonus_offer: bonusOffer,
        bonus_close: bonusClose,
        pen_crm_delay: penCrmDelay,
        pen_cancel: penCancel,
        pen_hot_lead: penHotLead,
        rca: rca.trim() || null,
        mentor_feedback: mentorFeedback.trim() || null,
      };
      if (dpa) await api(`/crm/dpa/${dpa.id}`, { method: "PUT", json: payload });
      else await api("/crm/dpa", { json: payload });
      await qc.invalidateQueries({ queryKey: QKEY });
      toast.success("فرم ارزیابی ذخیره شد");
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "ذخیره نشد");
    } finally {
      setBusy(false);
    }
  }

  return (
    <RingDialog open={open} onOpenChange={onOpenChange} icon={ClipboardCheck} title="فرم ارزیابی عملکرد روزانه" wide>
      <form onSubmit={save} className="flex flex-col gap-5">
        <div className="grid gap-3 sm:grid-cols-4">
          <Field label="نام مشاور" htmlFor="dpa-agent" className="sm:col-span-2">
            <Input id="dpa-agent" value={agentName} onChange={(e) => setAgentName(e.target.value)} autoFocus />
          </Field>
          <Field label="تخصص اصلی" htmlFor="dpa-role">
            <NativeSelect id="dpa-role" value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="hunter">Hunter</option>
              <option value="closer">Closer</option>
            </NativeSelect>
          </Field>
          <Field label="هدف امتیازی" htmlFor="dpa-target">
            <Input id="dpa-target" type="number" dir="ltr" min={0} value={target} onChange={(e) => setTarget(num(e.target.value) || 100)} className="tabular" />
          </Field>
          <Field label="تاریخ (شمسی)" className="sm:col-span-2">
            <JalaliDateInput value={dateJalali} onChange={setDateJalali} aria-label="تاریخ ارزیابی" />
          </Field>
          <Field label="فایل‌های جدید" htmlFor="dpa-new-files">
            <CounterInput id="dpa-new-files" value={newFiles} onChange={setNewFiles} />
          </Field>
          <Field label="تعداد بازدید" htmlFor="dpa-showings-count">
            <CounterInput id="dpa-showings-count" value={showingsCount} onChange={setShowingsCount} />
          </Field>
          <Field label="آفرهای دریافتی" htmlFor="dpa-offers-count">
            <CounterInput id="dpa-offers-count" value={offersCount} onChange={setOffersCount} />
          </Field>
          <Field label="قرارداد نهایی" htmlFor="dpa-closed-count">
            <CounterInput id="dpa-closed-count" value={closedCount} onChange={setClosedCount} />
          </Field>
        </div>

        <Section title="گام‌های عملیاتی پایه" bodyClassName="p-0 pt-1">
          <Table aria-label="گام‌های عملیاتی پایه">
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>بازه</TableHead>
                <TableHead>شرح</TableHead>
                <TableHead className="w-16 text-center">وزن</TableHead>
                <TableHead className="w-14 text-center">انجام</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {BASE_TASKS.map((t) => (
                <TableRow key={t.key} className={cn(t.rest && "text-muted-foreground")}>
                  <TableCell dir="ltr" className="text-end tabular">{t.time}</TableCell>
                  <TableCell className="max-w-[280px] whitespace-normal">{t.label}</TableCell>
                  <TableCell className="text-center tabular">{faNum(t.weight)}</TableCell>
                  <TableCell className="text-center">
                    {!t.rest && (
                      <Checkbox
                        checked={!!baseTasks[t.key]}
                        onCheckedChange={(v) => setBaseTasks((s) => ({ ...s, [t.key]: v === true }))}
                        aria-label={t.label}
                      />
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Section>

        <Section title="فعالیت‌های امتیازی" hint="امتیاز = تعداد × ارزش هر مورد؛ «خودکار» را سیستم از روی کارهای CRM پر می‌کند" bodyClassName="p-0 pt-1">
          <Table aria-label="فعالیت‌های امتیازی">
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead>فعالیت</TableHead>
                <TableHead className="w-16 text-center">ارزش</TableHead>
                <TableHead className="w-20 text-center">خودکار</TableHead>
                <TableHead className="w-24 text-center">دستی</TableHead>
                <TableHead className="w-16 text-center">امتیاز</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {activityDefs.map((a) => {
                const autoN = auto[a.key] || 0;
                const total = autoN + (manual[a.key] || 0);
                return (
                  <TableRow key={a.key}>
                    <TableCell>{a.label}</TableCell>
                    <TableCell className="text-center tabular">{faNum(a.points)}</TableCell>
                    <TableCell className="text-center">
                      <ToneBadge tone={autoN ? "success" : "neutral"}>{faNum(autoN)}</ToneBadge>
                    </TableCell>
                    <TableCell className="text-center">
                      <CounterInput
                        value={manual[a.key] || 0} onChange={(n) => setManual((s) => ({ ...s, [a.key]: n }))}
                        aria-label={`تعداد دستی — ${a.label}`}
                      />
                    </TableCell>
                    <TableCell className="text-center font-semibold tabular">{faNum(total * a.points)}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </Section>

        <div className="grid gap-4 sm:grid-cols-2">
          <Section title="امتیاز انفجار" className="border-success/30">
            <div className="flex flex-col gap-2 text-sm">
              <label className="flex items-center gap-2">
                <CounterInput value={bonusExclusive} onChange={setBonusExclusive} />
                × ثبت فایل انحصاری <b className="text-success">+۳۰</b>
              </label>
              <label className="flex items-center gap-2">
                <CounterInput value={bonusOffer} onChange={setBonusOffer} />
                × دریافت آفر کتبی و بیعانه <b className="text-success">+۲۰</b>
              </label>
              <label className="flex items-center gap-2">
                <CounterInput value={bonusClose} onChange={setBonusClose} />
                × بستن قرارداد نهایی <b className="text-success">+۵۰</b>
              </label>
            </div>
          </Section>
          <Section title="اخطار سیستم" className="border-destructive/30">
            <div className="flex flex-col gap-2 text-sm">
              <label className="flex items-center gap-2">
                <CounterInput value={penCrmDelay} onChange={setPenCrmDelay} />
                × تأخیر در ثبت CRM <b className="text-destructive">-۱۰</b>
              </label>
              <label className="flex items-center gap-2">
                <CounterInput value={penCancel} onChange={setPenCancel} />
                × کنسل شدن بازدید مقصر مشاور <b className="text-destructive">-۱۵</b>
              </label>
              <label className="flex items-center gap-2">
                <CounterInput value={penHotLead} onChange={setPenHotLead} />
                × عدم پیگیری لید داغ <b className="text-destructive">-۲۰</b>
              </label>
            </div>
          </Section>
        </div>

        <Section>
          <div className="flex flex-wrap justify-around gap-4 text-center">
            <div><div className="text-xs text-muted-foreground">پایه</div><div className="text-xl font-black tabular">{faNum(score.base)}</div></div>
            <div><div className="text-xs text-muted-foreground">فعالیت‌ها</div><div className="text-xl font-black text-info tabular">+{faNum(score.activity)}</div></div>
            <div><div className="text-xs text-muted-foreground">بونوس</div><div className="text-xl font-black text-success tabular">+{faNum(score.bonus)}</div></div>
            <div><div className="text-xs text-muted-foreground">جریمه</div><div className="text-xl font-black text-destructive tabular">-{faNum(score.penalty)}</div></div>
            <div>
              <div className="text-xs text-muted-foreground">امتیاز نهایی امروز</div>
              <div className={cn("text-2xl font-black tabular", score.total >= (target || 100) ? "text-success" : "text-primary")}>
                {faNum(score.total)}
              </div>
            </div>
          </div>
        </Section>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="تحلیل موانع (RCA)" htmlFor="dpa-rca">
            <Textarea id="dpa-rca" rows={3} value={rca} onChange={(e) => setRca(e.target.value)} />
          </Field>
          <Field label="بازخورد روزانهٔ منتور" htmlFor="dpa-mentor">
            <Textarea id="dpa-mentor" rows={3} value={mentorFeedback} onChange={(e) => setMentorFeedback(e.target.value)} />
          </Field>
        </div>

        <div className="grid gap-2">
          <Button type="submit" disabled={busy} className="w-full">
            {busy && <Loader2 className="size-4 animate-spin" />}
            ذخیرهٔ فرم
          </Button>
          <Button type="button" variant="ghost" className="w-full" onClick={() => onOpenChange(false)}>انصراف</Button>
        </div>
      </form>
    </RingDialog>
  );
}

/* ───────────────────────── page ───────────────────────── */

export function DpaTab() {
  const [search, setSearch] = useState("");
  const [dateFilter, setDateFilter] = useState<Date | null>(null);
  const [dialogDpa, setDialogDpa] = useState<Dpa | null | undefined>(undefined);
  const qc = useQueryClient();
  const confirm = useConfirm();

  const dateJalali = dateFilter ? formatJalali(dateFilter) : "";
  const filters = { search, date_jalali: dateJalali, limit: 100 };

  const q = useQuery({
    queryKey: [...QKEY, search, dateJalali],
    queryFn: () => api<{ items: Dpa[]; total: number }>(`/crm/dpa${qs(filters)}`),
  });

  async function remove(d: Dpa) {
    if (!(await confirm({ title: "حذف رکورد ارزیابی", description: `رکورد «${d.agent_name}» حذف شود؟`, confirm: "حذف", danger: true, icon: Trash2 }))) return;
    try {
      await api(`/crm/dpa/${d.id}`, { method: "DELETE" });
      await qc.invalidateQueries({ queryKey: QKEY });
      toast.success("رکورد حذف شد");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "حذف نشد");
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        icon={ClipboardCheck}
        title="ارزیابی روزانه"
        hint={q.data ? `${faNum(q.data.total)} رکورد` : undefined}
        actions={
          <Button className="gap-1.5" onClick={() => setDialogDpa(null)}>
            <Plus className="size-4" /> ارزیابی تازه
          </Button>
        }
      />

      <Reveal>
        <Section
          action={
            <Button asChild variant="outline" size="sm" className="gap-1.5">
              <a href={exportHref("/crm/dpa/export/excel", filters)} download>
                <Download className="size-3.5" /> خروجی اکسل
              </a>
            </Button>
          }
        >
          <Toolbar className="mb-4">
            <Field label="جستجوی مشاور" htmlFor="dpa-filter-search" className="w-52">
              <Input id="dpa-filter-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="نام مشاور…" />
            </Field>
            <Field label="تاریخ" className="w-44">
              <JalaliDateInput value={dateFilter} onChange={setDateFilter} aria-label="فیلتر تاریخ" placeholder="همهٔ تاریخ‌ها" />
            </Field>
          </Toolbar>

          {q.isPending ? (
            <ListSkeleton />
          ) : q.isError ? (
            <ErrorNote error={q.error} />
          ) : q.data.items.length === 0 ? (
            <Empty icon={ClipboardCheck} action={<Button size="sm" onClick={() => setDialogDpa(null)}>ارزیابی تازه</Button>}>
              هنوز فرمی ثبت نشده است.
            </Empty>
          ) : (
            <Table aria-label="فهرست ارزیابی روزانه">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>#</TableHead>
                  <TableHead>تاریخ</TableHead>
                  <TableHead>مشاور</TableHead>
                  <TableHead>نقش</TableHead>
                  <TableHead className="text-center">پایه</TableHead>
                  <TableHead className="text-center">فعالیت</TableHead>
                  <TableHead className="text-center">بونوس</TableHead>
                  <TableHead className="text-center">جریمه</TableHead>
                  <TableHead className="text-center">امتیاز کل</TableHead>
                  <TableHead className="text-center">هدف</TableHead>
                  <TableHead className="text-center">عملیات</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {q.data.items.map((d) => (
                  <TableRow key={d.id}>
                    <TableCell className="tabular">{faNum(d.id)}</TableCell>
                    <TableCell className="tabular">{d.date_jalali || "—"}</TableCell>
                    <TableCell className="font-medium">{d.agent_name}</TableCell>
                    <TableCell>{ROLE_LABEL[d.role] ?? d.role}</TableCell>
                    <TableCell className="text-center tabular">{faNum(d.base_score)}</TableCell>
                    <TableCell className="text-center text-info tabular">+{faNum(d.activity_score)}</TableCell>
                    <TableCell className="text-center text-success tabular">+{faNum(d.bonus_score)}</TableCell>
                    <TableCell className="text-center text-destructive tabular">-{faNum(d.penalty_score)}</TableCell>
                    <TableCell className="text-center">
                      <ToneBadge tone={d.total_score >= (d.target_points || 100) ? "success" : "warning"}>{faNum(d.total_score)}</ToneBadge>
                    </TableCell>
                    <TableCell className="text-center tabular">{faNum(d.target_points || 100)}</TableCell>
                    <TableCell>
                      <div className="flex justify-center gap-1">
                        <Button variant="ghost" size="icon" className="size-8" aria-label="ویرایش ارزیابی" onClick={() => setDialogDpa(d)}>
                          <PenLine className="size-4" />
                        </Button>
                        <Button variant="ghost" size="icon" className="size-8 text-destructive" aria-label="حذف ارزیابی" onClick={() => remove(d)}>
                          <Trash2 className="size-4" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Section>
      </Reveal>

      {dialogDpa !== undefined && (
        <DpaDialog open={dialogDpa !== undefined} onOpenChange={(o) => !o && setDialogDpa(undefined)} dpa={dialogDpa} />
      )}
    </div>
  );
}
