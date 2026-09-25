"use client";

// وظایف — task list, board and the create/edit dialog.
// GET/POST/PUT/DELETE /crm/tasks, quick done via PATCH /crm/tasks/{id}/status.

import {
  AlertTriangle, CalendarClock, Check, CircleDashed, Kanban, ListChecks, Loader2, PenLine, Plus, Table2,
  Trash2, User,
} from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/components/toaster";
import { Reveal, Tilt } from "@/components/viz";
import { JalaliDateInput } from "@/components/panel/date-input";
import {
  Empty, ErrorNote, Field, LabelBadge, ListSkeleton, NativeSelect, PageHeader, RingDialog, Section,
  Toolbar, useConfirm,
} from "@/components/panel/kit";
import { api, ApiError } from "@/lib/api";
import { TASK_PRIORITY, TASK_STATUS, qs } from "@/lib/crm";
import { faDate, faNum } from "@/lib/format";

type Task = {
  id: number;
  title: string;
  description: string | null;
  due_date: string | null;
  priority: string;
  status: string;
  contact_id: number | null;
  deal_id: number | null;
  assigned_to: string | null;
  created_at: string | null;
  updated_at: string | null;
};

const BOARD_COLUMNS = ["todo", "in_progress", "done"] as const;
const QKEY = ["crm", "tasks"] as const;

function isOverdue(t: Task) {
  return !!t.due_date && t.status !== "done" && new Date(t.due_date).getTime() < Date.now();
}

function due(iso: string) {
  return faDate(new Date(iso), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/* ───────────────────────── create / edit dialog ───────────────────────── */

function TaskDialog({
  open, onOpenChange, task,
}: { open: boolean; onOpenChange: (o: boolean) => void; task: Task | null }) {
  const qc = useQueryClient();
  const [title, setTitle] = useState(task?.title ?? "");
  const [description, setDescription] = useState(task?.description ?? "");
  const [due_date, setDueDate] = useState<Date | null>(task?.due_date ? new Date(task.due_date) : null);
  const [priority, setPriority] = useState(task?.priority ?? "medium");
  const [status, setStatus] = useState(task?.status ?? "todo");
  const [assigned_to, setAssignedTo] = useState(task?.assigned_to ?? "");
  const [busy, setBusy] = useState(false);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) {
      toast.error("عنوان وظیفه لازم است");
      return;
    }
    setBusy(true);
    try {
      const payload = {
        title: title.trim(),
        description: description.trim() || null,
        due_date: due_date ? due_date.toISOString() : null,
        priority,
        status,
        assigned_to: assigned_to.trim() || null,
      };
      if (task) await api(`/crm/tasks/${task.id}`, { method: "PUT", json: payload });
      else await api("/crm/tasks", { json: payload });
      await qc.invalidateQueries({ queryKey: QKEY });
      toast.success(task ? "وظیفه ذخیره شد" : "وظیفه ثبت شد");
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "ذخیره نشد");
    } finally {
      setBusy(false);
    }
  }

  return (
    <RingDialog
      open={open}
      onOpenChange={onOpenChange}
      icon={task ? PenLine : Plus}
      title={task ? "ویرایش وظیفه" : "وظیفهٔ تازه"}
      wide
    >
      <form onSubmit={save} className="grid gap-3 sm:grid-cols-2">
        <Field label="عنوان" htmlFor="task-title" className="sm:col-span-2">
          <Input id="task-title" value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
        </Field>
        <Field label="توضیحات" htmlFor="task-desc" className="sm:col-span-2">
          <Textarea id="task-desc" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <Field label="سررسید">
          <JalaliDateInput value={due_date} onChange={setDueDate} withTime aria-label="سررسید وظیفه" />
        </Field>
        <Field label="مسئول" htmlFor="task-assigned">
          <Input id="task-assigned" value={assigned_to} onChange={(e) => setAssignedTo(e.target.value)} placeholder="نام مشاور" />
        </Field>
        <Field label="اولویت" htmlFor="task-priority">
          <NativeSelect id="task-priority" value={priority} onChange={(e) => setPriority(e.target.value)}>
            {Object.entries(TASK_PRIORITY).map(([k, v]) => (
              <option key={k} value={k}>{v.label}</option>
            ))}
          </NativeSelect>
        </Field>
        <Field label="وضعیت" htmlFor="task-status">
          <NativeSelect id="task-status" value={status} onChange={(e) => setStatus(e.target.value)}>
            {Object.entries(TASK_STATUS).map(([k, v]) => (
              <option key={k} value={k}>{v.label}</option>
            ))}
          </NativeSelect>
        </Field>
        <div className="grid gap-2 sm:col-span-2">
          <Button type="submit" disabled={busy} className="w-full">
            {busy && <Loader2 className="size-4 animate-spin" />}
            {task ? "ذخیرهٔ تغییرات" : "ثبت وظیفه"}
          </Button>
          <Button type="button" variant="ghost" className="w-full" onClick={() => onOpenChange(false)}>انصراف</Button>
        </div>
      </form>
    </RingDialog>
  );
}

/* ───────────────────────── row actions (shared) ───────────────────────── */

function useTaskActions() {
  const qc = useQueryClient();
  const confirm = useConfirm();

  async function toggleDone(t: Task) {
    const next = t.status === "done" ? "todo" : "done";
    try {
      await api(`/crm/tasks/${t.id}/status`, { method: "PATCH", json: { status: next } });
      await qc.invalidateQueries({ queryKey: QKEY });
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "ثبت نشد");
    }
  }

  async function setStatus(t: Task, status: string) {
    try {
      await api(`/crm/tasks/${t.id}/status`, { method: "PATCH", json: { status } });
      await qc.invalidateQueries({ queryKey: QKEY });
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "ثبت نشد");
    }
  }

  async function remove(t: Task) {
    if (!(await confirm({ title: "حذف وظیفه", description: `«${t.title}» حذف شود؟`, confirm: "حذف", danger: true, icon: Trash2 }))) return;
    try {
      await api(`/crm/tasks/${t.id}`, { method: "DELETE" });
      await qc.invalidateQueries({ queryKey: QKEY });
      toast.success("وظیفه حذف شد");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "حذف نشد");
    }
  }

  return { toggleDone, setStatus, remove };
}

/* ───────────────────────── table view ───────────────────────── */

function TasksTable({ items, onEdit }: { items: Task[]; onEdit: (t: Task) => void }) {
  const { toggleDone, remove } = useTaskActions();
  return (
    <Table aria-label="فهرست وظایف">
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead className="w-9" />
          <TableHead>عنوان</TableHead>
          <TableHead>اولویت</TableHead>
          <TableHead>وضعیت</TableHead>
          <TableHead>سررسید</TableHead>
          <TableHead>مسئول</TableHead>
          <TableHead className="text-center">عملیات</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((t) => {
          const overdue = isOverdue(t);
          return (
            <TableRow key={t.id} className={cn(overdue && "bg-destructive/5")}>
              <TableCell>
                <Checkbox
                  checked={t.status === "done"}
                  onCheckedChange={() => toggleDone(t)}
                  aria-label={t.status === "done" ? "انجام‌شده — لغو" : "علامت‌گذاری به‌عنوان انجام‌شده"}
                />
              </TableCell>
              <TableCell className="max-w-[260px]">
                <div className="truncate font-medium">{t.title}</div>
                {t.description && <div className="truncate text-xs text-muted-foreground">{t.description}</div>}
              </TableCell>
              <TableCell><LabelBadge map={TASK_PRIORITY} value={t.priority} /></TableCell>
              <TableCell><LabelBadge map={TASK_STATUS} value={t.status} /></TableCell>
              <TableCell className="tabular">
                {t.due_date ? (
                  <span className={cn("inline-flex items-center gap-1", overdue && "font-semibold text-destructive")}>
                    {overdue && <AlertTriangle className="size-3.5" />}
                    {due(t.due_date)}
                  </span>
                ) : "—"}
              </TableCell>
              <TableCell>{t.assigned_to || "—"}</TableCell>
              <TableCell>
                <div className="flex justify-center gap-1">
                  <Button variant="ghost" size="icon" className="size-8" aria-label="ویرایش وظیفه" onClick={() => onEdit(t)}>
                    <PenLine className="size-4" />
                  </Button>
                  <Button variant="ghost" size="icon" className="size-8 text-destructive" aria-label="حذف وظیفه" onClick={() => remove(t)}>
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

/* ───────────────────────── board view ───────────────────────── */

const COLUMN_LABEL: Record<string, string> = { todo: "انجام نشده", in_progress: "در حال انجام", done: "انجام شده" };
const COLUMN_ICON: Record<string, React.ComponentType<{ className?: string }>> = {
  todo: CircleDashed, in_progress: Loader2, done: Check,
};

function TaskCard({ t, onEdit }: { t: Task; onEdit: (t: Task) => void }) {
  const { setStatus, remove } = useTaskActions();
  const overdue = isOverdue(t);
  const idx = BOARD_COLUMNS.indexOf(t.status as (typeof BOARD_COLUMNS)[number]);
  return (
    <Tilt max={4} className={cn(
      "flex flex-col gap-2 rounded-xl border bg-card p-3 shadow-sm",
      "dark:bg-linear-to-b dark:from-white/[0.04] dark:to-transparent",
      overdue && "border-destructive/40",
    )}>
      <div className="flex items-start justify-between gap-2">
        <button type="button" onClick={() => onEdit(t)} className="text-start text-sm font-semibold hover:underline">
          {t.title}
        </button>
        <LabelBadge map={TASK_PRIORITY} value={t.priority} />
      </div>
      {t.due_date && (
        <span className={cn("inline-flex w-fit items-center gap-1 text-[11px] tabular text-muted-foreground", overdue && "font-semibold text-destructive")}>
          <CalendarClock className="size-3" />
          {due(t.due_date)}
        </span>
      )}
      {t.assigned_to && (
        <span className="inline-flex w-fit items-center gap-1 text-[11px] text-muted-foreground">
          <User className="size-3" />
          {t.assigned_to}
        </span>
      )}
      <div className="mt-1 flex items-center justify-between gap-1">
        <div className="flex gap-1">
          {idx > 0 && (
            <Button variant="ghost" size="xs" onClick={() => setStatus(t, BOARD_COLUMNS[idx - 1])}>
              {COLUMN_LABEL[BOARD_COLUMNS[idx - 1]]} ←
            </Button>
          )}
          {idx < BOARD_COLUMNS.length - 1 && (
            <Button variant="ghost" size="xs" onClick={() => setStatus(t, BOARD_COLUMNS[idx + 1])}>
              → {COLUMN_LABEL[BOARD_COLUMNS[idx + 1]]}
            </Button>
          )}
        </div>
        <Button variant="ghost" size="icon-xs" className="text-destructive" aria-label="حذف وظیفه" onClick={() => remove(t)}>
          <Trash2 className="size-3.5" />
        </Button>
      </div>
    </Tilt>
  );
}

function TasksBoard({ items, onEdit }: { items: Task[]; onEdit: (t: Task) => void }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
      {BOARD_COLUMNS.map((col) => {
        const list = items.filter((t) => t.status === col);
        const Icon = COLUMN_ICON[col];
        return (
          <div key={col} className="flex min-w-0 flex-col gap-3 rounded-xl border bg-muted/30 p-3">
            <div className="flex items-center gap-1.5 text-sm font-bold">
              <Icon className="size-4 text-muted-foreground" />
              {COLUMN_LABEL[col]}
              <span className="ms-auto text-xs font-normal text-muted-foreground tabular">{faNum(list.length)}</span>
            </div>
            <div className="flex flex-col gap-2">
              {list.length === 0 ? (
                <p className="px-1 py-4 text-center text-xs text-muted-foreground">خالی است</p>
              ) : list.map((t) => <TaskCard key={t.id} t={t} onEdit={onEdit} />)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ───────────────────────── page ───────────────────────── */

export function TasksTab() {
  const [status, setStatus] = useState("");
  const [priority, setPriority] = useState("");
  const [view, setView] = useState<"table" | "board">("table");
  const [dialogTask, setDialogTask] = useState<Task | null | undefined>(undefined);

  const q = useQuery({
    queryKey: [...QKEY, status, priority],
    queryFn: () => api<{ items: Task[]; total: number }>(`/crm/tasks${qs({ status, priority, limit: 200 })}`),
  });

  const overdueCount = (q.data?.items ?? []).filter(isOverdue).length;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        icon={ListChecks}
        title="وظایف"
        hint={q.data ? `${faNum(q.data.total)} وظیفه${overdueCount ? ` — ${faNum(overdueCount)} عقب‌افتاده` : ""}` : undefined}
        actions={
          <Button className="gap-1.5" onClick={() => setDialogTask(null)}>
            <Plus className="size-4" /> وظیفهٔ تازه
          </Button>
        }
      />

      <Reveal>
        <Section
          action={
            <div role="group" aria-label="نمای فهرست" className="inline-flex h-8 items-center rounded-lg bg-muted p-[3px]">
              <button type="button" aria-pressed={view === "table"} onClick={() => setView("table")}
                className={cn("flex h-full items-center gap-1 rounded-md px-2.5 text-xs font-medium text-muted-foreground outline-none", view === "table" && "bg-background text-foreground shadow-sm dark:bg-input/30")}>
                <Table2 className="size-3.5" /> فهرست
              </button>
              <button type="button" aria-pressed={view === "board"} onClick={() => setView("board")}
                className={cn("flex h-full items-center gap-1 rounded-md px-2.5 text-xs font-medium text-muted-foreground outline-none", view === "board" && "bg-background text-foreground shadow-sm dark:bg-input/30")}>
                <Kanban className="size-3.5" /> تابلو
              </button>
            </div>
          }
        >
          <Toolbar className="mb-4">
            <Field label="وضعیت" htmlFor="tasks-filter-status" className="w-40">
              <NativeSelect id="tasks-filter-status" value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="">همه</option>
                {Object.entries(TASK_STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
              </NativeSelect>
            </Field>
            <Field label="اولویت" htmlFor="tasks-filter-priority" className="w-40">
              <NativeSelect id="tasks-filter-priority" value={priority} onChange={(e) => setPriority(e.target.value)}>
                <option value="">همه</option>
                {Object.entries(TASK_PRIORITY).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
              </NativeSelect>
            </Field>
          </Toolbar>

          {q.isPending ? (
            <ListSkeleton />
          ) : q.isError ? (
            <ErrorNote error={q.error} />
          ) : q.data.items.length === 0 ? (
            <Empty icon={ListChecks} action={<Button size="sm" onClick={() => setDialogTask(null)}>وظیفهٔ تازه</Button>}>
              وظیفه‌ای ثبت نشده است.
            </Empty>
          ) : view === "table" ? (
            <TasksTable items={q.data.items} onEdit={setDialogTask} />
          ) : (
            <TasksBoard items={q.data.items} onEdit={setDialogTask} />
          )}
        </Section>
      </Reveal>

      {dialogTask !== undefined && (
        <TaskDialog open={dialogTask !== undefined} onOpenChange={(o) => !o && setDialogTask(undefined)} task={dialogTask} />
      )}
    </div>
  );
}
