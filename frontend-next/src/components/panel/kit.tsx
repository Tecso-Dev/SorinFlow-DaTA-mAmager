"use client";

// The building blocks every section of the new panel is made of, so that a
// page written by one person looks like a page written by another:
// headers, cards, badges, empty states, pagination, the OTP-look dialog,
// confirm (never window.confirm), and form fields.

import { ChevronLeft, ChevronRight, type LucideIcon } from "lucide-react";
import { createContext, useCallback, useContext, useRef, useState } from "react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api";
import type { Labeled, Tone } from "@/lib/crm";
import { faNum } from "@/lib/format";

/* ───────────────────────── page structure ───────────────────────── */

/** A section's title row: an isometric badge, title, hint and actions. */
export function PageHeader({
  icon: Icon, title, hint, actions,
}: { icon: LucideIcon; title: string; hint?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="flex items-center gap-4">
        <IsoBadge icon={Icon} />
        <div className="min-w-0">
          <h1 className="text-2xl font-black tracking-tight">{title}</h1>
          {hint && <p className="mt-0.5 text-sm text-muted-foreground">{hint}</p>}
        </div>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

/** A small extruded block holding the section's icon: the 3D touch every
 *  section header carries. Stacked layers, CSS only, so it costs nothing on
 *  a phone and is the same under reduced motion. */
export function IsoBadge({ icon: Icon, className }: { icon: LucideIcon; className?: string }) {
  return (
    <div className={cn("relative size-14 shrink-0", className)} aria-hidden>
      {[5, 4, 3, 2, 1].map((k) => (
        <div
          key={k}
          className="absolute inset-0 rounded-2xl bg-violet-900/70 dark:bg-violet-950"
          style={{ transform: `translate(${k * 1.2}px, ${k * 1.2}px)`, opacity: 0.35 + (5 - k) * 0.12 }}
        />
      ))}
      <div className="absolute inset-0 rounded-2xl bg-linear-to-br from-indigo-400 via-indigo-500 to-violet-600 shadow-[0_12px_28px_-8px_rgb(99_102_241/0.8)]" />
      <div className="absolute inset-x-2 top-1 h-1/3 rounded-full bg-white/25 blur-[6px]" />
      <Icon className="absolute inset-0 m-auto size-6 text-white drop-shadow" />
    </div>
  );
}

/** A card with an optional title row, in the «شب نیلی» look. */
export function Section({
  title, hint, action, className, bodyClassName, children,
}: {
  title?: React.ReactNode; hint?: React.ReactNode; action?: React.ReactNode; className?: string;
  bodyClassName?: string; children: React.ReactNode;
}) {
  return (
    <section
      className={cn(
        "flex min-w-0 flex-col rounded-2xl border bg-card text-card-foreground shadow-sm",
        "dark:bg-linear-to-b dark:from-white/[0.035] dark:to-white/[0.008] dark:shadow-none",
        className,
      )}
    >
      {(title || action) && (
        <header className="flex items-start justify-between gap-3 px-5 pt-4">
          <div className="min-w-0">
            {title && <h2 className="text-[15px] font-bold">{title}</h2>}
            {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
          </div>
          {action}
        </header>
      )}
      <div className={cn("flex-1 p-5 pt-3", bodyClassName)}>{children}</div>
    </section>
  );
}

/** A row of filters above a list; wraps on a phone. */
export function Toolbar({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("flex flex-wrap items-end gap-2", className)}>{children}</div>;
}

/* ───────────────────────── small pieces ───────────────────────── */

const TONE: Record<Tone, string> = {
  neutral: "bg-muted text-muted-foreground",
  info: "bg-info/12 text-info",
  warning: "bg-warning/15 text-warning",
  success: "bg-success/12 text-success",
  danger: "bg-destructive/12 text-destructive",
  primary: "bg-primary/12 text-primary",
  violet: "bg-chart-5/15 text-chart-5",
};

export function ToneBadge({ tone = "neutral", children, className }: { tone?: Tone; children: React.ReactNode; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold", TONE[tone], className)}>
      {children}
    </span>
  );
}

/** A stored value through its label map; an unknown value shows as it is. */
export function LabelBadge({ map, value }: { map: Record<string, Labeled>; value: string | null | undefined }) {
  if (!value) return <span className="text-muted-foreground">—</span>;
  const l = map[value];
  return <ToneBadge tone={l?.tone ?? "neutral"}>{l?.label ?? value}</ToneBadge>;
}

/** Nothing to show: says so, with a small isometric stack. */
export function Empty({ children, icon: Icon, action }: { children: React.ReactNode; icon?: LucideIcon; action?: React.ReactNode }) {
  return (
    <div className="flex min-h-40 flex-col items-center justify-center gap-3 rounded-xl border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
      {Icon && <IsoBadge icon={Icon} className="scale-75 opacity-80" />}
      <div>{children}</div>
      {action}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  return <Empty>{error instanceof ApiError ? error.message : "بارگیری ناموفق بود"}</Empty>;
}

export function ListSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-3" aria-busy="true" aria-label="در حال بارگیری">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-3">
          <Skeleton className="size-9 rounded-full" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-3 w-2/3" />
            <Skeleton className="h-3 w-1/3" />
          </div>
        </div>
      ))}
    </div>
  );
}

/** «۱ ۲ … ۹ ۱۰» with previous/next; `page` is 1-based. */
export function Pagination({ page, pages, onPage }: { page: number; pages: number; onPage: (p: number) => void }) {
  if (pages <= 1) return null;
  const nums = new Set([1, pages, page - 1, page, page + 1].filter((p) => p >= 1 && p <= pages));
  const list = [...nums].sort((a, b) => a - b);
  return (
    <nav aria-label="صفحه‌ها" className="flex flex-wrap items-center justify-center gap-1">
      {/* RTL: «previous» points right */}
      <Button variant="ghost" size="icon" className="size-8" disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="صفحهٔ قبل">
        <ChevronRight className="size-4" />
      </Button>
      {list.map((p, i) => (
        <span key={p} className="flex items-center gap-1">
          {i > 0 && p - list[i - 1] > 1 && <span className="px-1 text-muted-foreground">…</span>}
          <Button
            variant={p === page ? "default" : "ghost"}
            size="sm"
            className="h-8 min-w-8 px-2 tabular"
            aria-current={p === page ? "page" : undefined}
            onClick={() => onPage(p)}
          >
            {faNum(p)}
          </Button>
        </span>
      ))}
      <Button variant="ghost" size="icon" className="size-8" disabled={page >= pages} onClick={() => onPage(page + 1)} aria-label="صفحهٔ بعد">
        <ChevronLeft className="size-4" />
      </Button>
    </nav>
  );
}

/** A labelled field; `hint` under it, `error` in its place when there is one. */
export function Field({
  label, htmlFor, hint, error, className, children,
}: { label: string; htmlFor?: string; hint?: string; error?: string; className?: string; children: React.ReactNode }) {
  return (
    <div className={cn("grid gap-1.5 text-start", className)}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {error ? <p className="text-xs text-destructive">{error}</p> : hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

/** A native select in the input's look (reliable on phones, RTL for free). */
export function NativeSelect({ className, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={cn(
        "h-9 w-full min-w-0 rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none",
        "focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50 dark:bg-input/30",
        "[&>option]:bg-popover [&>option]:text-popover-foreground",
        className,
      )}
    />
  );
}

/* ───────────────────────── dialogs ───────────────────────── */

/**
 * The OTP dialog's look: a 62px gradient ring holding an icon, a radial glow,
 * title and description, the body, and a column of full-width buttons.
 * `wide` for forms with many fields (they then lay out in two columns).
 */
export function RingDialog({
  open, onOpenChange, icon: Icon, title, description, wide, children, footer, tone = "primary",
}: {
  open: boolean; onOpenChange: (o: boolean) => void; icon: LucideIcon; title: string; description?: React.ReactNode;
  wide?: boolean; children?: React.ReactNode; footer?: React.ReactNode; tone?: "primary" | "danger";
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={cn("max-h-[92dvh] overflow-y-auto", wide ? "sm:max-w-2xl" : "sm:max-w-sm")}>
        <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-40 bg-[radial-gradient(60%_100%_at_50%_0%,var(--glow-1),transparent)]" />
        <div className="relative flex flex-col items-center gap-4 text-center">
          <div
            className={cn(
              "relative grid size-[62px] place-items-center rounded-full shadow-[0_0_40px_-6px_rgb(99_102_241/0.8)]",
              tone === "danger" ? "bg-linear-to-br from-rose-500 to-red-600" : "bg-linear-to-br from-indigo-500 to-violet-600",
            )}
          >
            <div className="absolute inset-[3px] rounded-full bg-card" />
            <Icon className={cn("relative size-6", tone === "danger" ? "text-destructive" : "text-primary")} />
          </div>
          <div>
            <DialogTitle className="text-lg font-black">{title}</DialogTitle>
            {description ? (
              <DialogDescription className="mt-1 text-sm leading-6">{description}</DialogDescription>
            ) : (
              <DialogDescription className="sr-only">{title}</DialogDescription>
            )}
          </div>
          {children && <div className="w-full text-start">{children}</div>}
          {footer && <div className="grid w-full gap-2">{footer}</div>}
        </div>
      </DialogContent>
    </Dialog>
  );
}

type ConfirmOptions = {
  title: string;
  description?: React.ReactNode;
  confirm?: string;
  cancel?: string;
  danger?: boolean;
  icon?: LucideIcon;
};
type Ask = (o: ConfirmOptions) => Promise<boolean>;
const ConfirmContext = createContext<Ask | null>(null);

/** Mounted once in the shell; useConfirm() then asks in the OTP look. */
export function ConfirmProvider({ children, fallbackIcon }: { children: React.ReactNode; fallbackIcon: LucideIcon }) {
  const [opts, setOpts] = useState<ConfirmOptions | null>(null);
  const resolver = useRef<((v: boolean) => void) | null>(null);
  const ask = useCallback<Ask>((o) => {
    resolver.current?.(false);
    setOpts(o);
    return new Promise<boolean>((res) => {
      resolver.current = res;
    });
  }, []);
  const done = (v: boolean) => {
    resolver.current?.(v);
    resolver.current = null;
    setOpts(null);
  };
  return (
    <ConfirmContext.Provider value={ask}>
      {children}
      <RingDialog
        open={!!opts}
        onOpenChange={(o) => !o && done(false)}
        icon={opts?.icon ?? fallbackIcon}
        tone={opts?.danger ? "danger" : "primary"}
        title={opts?.title ?? ""}
        description={opts?.description}
        footer={
          <>
            <Button variant={opts?.danger ? "destructive" : "default"} className="w-full" onClick={() => done(true)}>
              {opts?.confirm ?? "تأیید"}
            </Button>
            <Button variant="ghost" className="w-full" onClick={() => done(false)}>
              {opts?.cancel ?? "انصراف"}
            </Button>
          </>
        }
      />
    </ConfirmContext.Provider>
  );
}

/** `if (await confirm({title: "حذف شود؟", danger: true})) …` */
export function useConfirm(): Ask {
  const ask = useContext(ConfirmContext);
  if (!ask) throw new Error("useConfirm outside ConfirmProvider");
  return ask;
}
