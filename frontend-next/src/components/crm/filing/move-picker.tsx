"use client";

// «انتقال به…» — pick a binder or folder (or none) for one or several files.

import { FolderSymlink, FolderTree, Inbox } from "lucide-react";
import { cn } from "cn";
import { RingDialog } from "@/components/panel/kit";
import { faNum } from "@/lib/format";
import type { Cabinet } from "./types";

export function MovePicker({
  open, onOpenChange, cabinets, onPick,
}: { open: boolean; onOpenChange: (o: boolean) => void; cabinets: Cabinet[]; onPick: (binderId: number | null) => void }) {
  return (
    <RingDialog open={open} onOpenChange={onOpenChange} icon={FolderSymlink} title="انتقال به…" description="مقصد را انتخاب کنید">
      <div className="max-h-[50vh] overflow-y-auto rounded-lg border">
        <button
          type="button"
          onClick={() => onPick(null)}
          className="flex w-full items-center gap-2 border-b px-3 py-2 text-start text-sm outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Inbox className="size-4 text-muted-foreground" /> بدون زونکن (خارج کردن)
        </button>
        {cabinets.map((c) => (
          <div key={c.id}>
            <div className="flex items-center gap-2 bg-muted/40 px-3 py-1.5 text-xs font-semibold text-muted-foreground">
              <span className="size-2 rounded-full" style={{ background: c.color }} /> {c.name}
            </div>
            {c.binders.map((b) => (
              <div key={b.id}>
                <button
                  type="button"
                  onClick={() => onPick(b.id)}
                  className="flex w-full items-center gap-2 border-b px-3 py-2 text-start text-sm outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span className="size-2 shrink-0 rounded-full" style={{ background: b.color }} />
                  <span className="min-w-0 flex-1 truncate">{b.name}</span>
                  <span className="text-xs tabular text-muted-foreground">{faNum(b.file_count)}</span>
                </button>
                {(b.folders ?? []).map((f) => (
                  <button
                    key={f.id}
                    type="button"
                    onClick={() => onPick(f.id)}
                    className={cn("flex w-full items-center gap-2 border-b px-3 py-2 text-start text-sm outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring ps-8")}
                  >
                    <FolderTree className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="min-w-0 flex-1 truncate">{f.name}</span>
                    <span className="text-xs tabular text-muted-foreground">{faNum(f.file_count)}</span>
                  </button>
                ))}
              </div>
            ))}
          </div>
        ))}
      </div>
    </RingDialog>
  );
}
