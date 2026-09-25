"use client";

// The drawer a lead or a listing opens in: slides in from the left (the
// reading end of an RTL page), full width on a phone, a wide column on a
// desktop, with a sticky header and its own scroll.

import { X } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Sheet, SheetClose, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";

export function SideSheet({
  open, onOpenChange, title, description, header, children, className,
}: {
  open: boolean; onOpenChange: (o: boolean) => void; title: string; description?: string;
  /** replaces the plain title row */
  header?: React.ReactNode; children: React.ReactNode; className?: string;
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="left"
        showCloseButton={false}
        className={cn(
          "gap-0 p-0 data-[side=left]:w-full data-[side=left]:border-e data-[side=left]:sm:max-w-2xl",
          "dark:bg-linear-to-b dark:from-primary/[0.06] dark:to-popover",
          className,
        )}
      >
        <div className="sticky top-0 z-10 border-b bg-popover/90 px-4 py-3 backdrop-blur sm:px-5">
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              {header ?? <SheetTitle className="text-lg font-black">{title}</SheetTitle>}
              {header && <SheetTitle className="sr-only">{title}</SheetTitle>}
              <SheetDescription className={cn(!description && "sr-only")}>{description ?? title}</SheetDescription>
            </div>
            <SheetClose asChild>
              <Button variant="ghost" size="icon-sm" aria-label="بستن">
                <X />
              </Button>
            </SheetClose>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-5">{children}</div>
      </SheetContent>
    </Sheet>
  );
}
