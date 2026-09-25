"use client";

// One فایل (a Property row) on the grid. A checkbox for bulk selection, a
// serial badge, pin/private/archived/draft marks, and quick actions.

import {
  Archive, ArchiveRestore, Building2, FolderSymlink, Lock, MapPin, Pencil, Pin, Share2,
} from "lucide-react";
import { useState } from "react";
import { cn } from "cn";
import { Checkbox } from "@/components/ui/checkbox";
import { Tilt } from "@/components/viz";
import { price } from "@/lib/crm";
import { faNum } from "@/lib/format";
import type { Binder, FileBrief } from "./types";

export function FileCard({
  file, selected, onToggle, whereBinder, onEdit, onMove, onShare, onQuickAction,
}: {
  file: FileBrief;
  selected: boolean;
  onToggle: () => void;
  /** the folder/binder this file sits in, when it is not the one currently open */
  whereBinder?: Binder | null;
  onEdit: () => void;
  onMove: () => void;
  onShare: () => void;
  onQuickAction: (action: "pin" | "unpin" | "archive" | "unarchive") => void;
}) {
  const rentLine = file.listing_type === "rent"
    ? [file.deposit ? `رهن ${price(file.deposit)}` : null, file.rent_price ? `اجاره ${price(file.rent_price)}` : null].filter(Boolean).join(" · ") || "—"
    : price(file.price);
  // a scraped thumbnail's host can go missing or 404; the browser's own
  // broken-image icon renders at its tiny intrinsic size and spills out of
  // the card instead of filling it, so a failed load falls back to the
  // same placeholder a file with no thumbnail at all gets
  const [imgFailed, setImgFailed] = useState(false);

  return (
    <Tilt max={3}>
      <div
        draggable
        onDragStart={(e) => e.dataTransfer.setData("text/x-filing-file", String(file.id))}
        className={cn(
          "group relative flex flex-col gap-2 rounded-2xl border bg-card p-3 text-card-foreground shadow-sm transition-colors",
          "dark:bg-linear-to-b dark:from-white/[0.035] dark:to-white/[0.008]",
          selected && "ring-2 ring-primary",
        )}
      >
        <div className="flex items-start gap-2">
          <span className="grid size-6 shrink-0 place-items-center rounded-md">
            <Checkbox checked={selected} onCheckedChange={onToggle} aria-label="انتخاب فایل" />
          </span>
          <span className="rounded-md bg-muted px-1.5 py-0.5 text-[11px] font-bold tabular text-muted-foreground">
            {file.serial_no ? faNum(file.serial_no) : "—"}
          </span>
          <div className="flex flex-1 items-center justify-end gap-1 text-muted-foreground">
            {file.is_pinned && <Pin className="size-3.5 fill-warning text-warning" />}
            {file.is_private && <Lock className="size-3.5 text-info" />}
            {file.is_archived && <Archive className="size-3.5" />}
            {file.is_draft && <Pencil className="size-3.5 text-chart-5" />}
          </div>
        </div>

        {file.thumbnail_url && !imgFailed ? (
          // property photos: uncontrolled external/scraped sources, next/image's
          // domain allow-list would have to grow with every listing site
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={file.thumbnail_url} alt="" loading="lazy" onError={() => setImgFailed(true)}
            className="aspect-video w-full rounded-lg object-cover"
          />
        ) : (
          <div className="grid aspect-video w-full place-items-center rounded-lg bg-muted text-muted-foreground">
            <Building2 className="size-6" />
          </div>
        )}

        <div className="min-w-0">
          <p className="truncate text-sm font-bold" title={file.title}>{file.title}</p>
          <p className="mt-0.5 flex items-center gap-1 truncate text-xs text-muted-foreground">
            {(file.area || file.rooms != null) && (
              <span className="tabular">
                {file.area ? `${faNum(file.area)} متر` : ""}{file.area && file.rooms != null ? " · " : ""}{file.rooms != null ? `${faNum(file.rooms)} خواب` : ""}
              </span>
            )}
            {(file.district || file.city_name) && (
              <span className="flex items-center gap-0.5 truncate">
                <MapPin className="size-3 shrink-0" /> {file.district || file.city_name}
              </span>
            )}
          </p>
        </div>

        <p className="text-sm font-extrabold text-primary tabular">{rentLine}</p>

        {whereBinder && (
          <span className="flex w-fit items-center gap-1 truncate rounded-full px-2 py-0.5 text-[10px] font-medium" style={{ background: `color-mix(in oklab, ${whereBinder.color} 18%, transparent)`, color: whereBinder.color }}>
            <FolderSymlink className="size-3" /> {whereBinder.name}
          </span>
        )}

        {file.tags.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {file.tags.slice(0, 4).map((t) => (
              <span key={t} className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">{t}</span>
            ))}
          </div>
        )}

        <div className="mt-auto flex items-center gap-1 border-t pt-2 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
          <button type="button" onClick={onEdit} className="rounded-md p-1.5 text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring" title="ویرایش فایل" aria-label="ویرایش فایل">
            <Pencil className="size-4" />
          </button>
          <button type="button" onClick={onMove} className="rounded-md p-1.5 text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring" title="انتقال به زونکن" aria-label="انتقال به زونکن">
            <FolderSymlink className="size-4" />
          </button>
          <button type="button" onClick={() => onQuickAction(file.is_pinned ? "unpin" : "pin")} className="rounded-md p-1.5 text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring" title="سنجاق" aria-label="سنجاق">
            <Pin className="size-4" />
          </button>
          <button type="button" onClick={() => onQuickAction(file.is_archived ? "unarchive" : "archive")} className="rounded-md p-1.5 text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring" title="بایگانی" aria-label="بایگانی">
            {file.is_archived ? <ArchiveRestore className="size-4" /> : <Archive className="size-4" />}
          </button>
          <button type="button" onClick={onShare} className="rounded-md p-1.5 text-muted-foreground outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring" title="اشتراک‌گذاری" aria-label="اشتراک‌گذاری">
            <Share2 className="size-4" />
          </button>
        </div>
      </div>
    </Tilt>
  );
}
