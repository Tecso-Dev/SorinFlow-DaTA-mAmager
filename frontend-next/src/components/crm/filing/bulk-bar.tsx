"use client";

// The floating bar over a selection: POST /filing/files/bulk for everything
// except «انتقال به…», which opens the move picker first.

import { AnimatePresence, motion } from "motion/react";
import {
  Archive, ArchiveRestore, Eye, EyeOff, FileEdit, FolderSymlink, Lock, Pin, PinOff, Tag, Unlock, X,
} from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { RingDialog } from "@/components/panel/kit";
import { faNum } from "@/lib/format";

type Action = "pin" | "unpin" | "archive" | "unarchive" | "private" | "public" | "draft" | "undraft";

export function BulkBar({
  count, onAction, onMove, onTag, onClear,
}: {
  count: number;
  onAction: (a: Action) => void;
  onMove: () => void;
  onTag: (tags: string, remove: boolean) => void;
  onClear: () => void;
}) {
  const [tagOpen, setTagOpen] = useState<false | "tag" | "untag">(false);
  const [tagText, setTagText] = useState("");

  const btn = (icon: React.ReactNode, label: string, onClick: () => void, danger = false) => (
    <Button variant={danger ? "destructive" : "ghost"} size="sm" className="gap-1.5" onClick={onClick}>
      {icon} <span className="hidden sm:inline">{label}</span>
    </Button>
  );

  async function submitTag() {
    if (!tagText.trim()) return;
    onTag(tagText.trim(), tagOpen === "untag");
    setTagText("");
    setTagOpen(false);
  }

  return (
    <AnimatePresence>
      {count > 0 && (
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 24 }}
          transition={{ type: "spring", stiffness: 340, damping: 30 }}
          className="sticky bottom-3 z-20 mx-auto flex w-fit max-w-full flex-wrap items-center gap-1 rounded-2xl border bg-popover/95 p-1.5 text-popover-foreground shadow-xl ring-1 ring-foreground/10 backdrop-blur"
        >
          <span className="me-1 flex items-center gap-1 ps-2 text-sm font-bold tabular">
            {faNum(count)} انتخاب‌شده
          </span>
          {btn(<Pin className="size-4" />, "سنجاق", () => onAction("pin"))}
          {btn(<PinOff className="size-4" />, "برداشتن سنجاق", () => onAction("unpin"))}
          {btn(<Tag className="size-4" />, "برچسب", () => setTagOpen("tag"))}
          {btn(<Tag className="size-4 opacity-50" />, "حذف برچسب", () => setTagOpen("untag"))}
          {btn(<FolderSymlink className="size-4" />, "انتقال به…", onMove)}
          {btn(<Archive className="size-4" />, "بایگانی", () => onAction("archive"))}
          {btn(<ArchiveRestore className="size-4" />, "خارج از بایگانی", () => onAction("unarchive"))}
          {btn(<Lock className="size-4" />, "خصوصی", () => onAction("private"))}
          {btn(<Unlock className="size-4" />, "عمومی", () => onAction("public"))}
          {btn(<FileEdit className="size-4" />, "پیش‌نویس", () => onAction("draft"))}
          {btn(<Eye className="size-4" />, "خروج از پیش‌نویس", () => onAction("undraft"))}
          <Button variant="ghost" size="icon-sm" aria-label="پاک کردن انتخاب" onClick={onClear}>
            <X className="size-4" />
          </Button>

          <RingDialog
            open={!!tagOpen}
            onOpenChange={(o) => !o && setTagOpen(false)}
            icon={tagOpen === "untag" ? EyeOff : Tag}
            title={tagOpen === "untag" ? "حذف برچسب از فایل‌های انتخاب‌شده" : "افزودن برچسب به فایل‌های انتخاب‌شده"}
            footer={<Button className="w-full" onClick={submitTag}>{tagOpen === "untag" ? "حذف برچسب" : "افزودن"}</Button>}
          >
            <Input
              autoFocus value={tagText} onChange={(e) => setTagText(e.target.value)}
              placeholder="مثلاً: فوری، تخفیف‌دار" onKeyDown={(e) => e.key === "Enter" && submitTag()}
            />
          </RingDialog>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
