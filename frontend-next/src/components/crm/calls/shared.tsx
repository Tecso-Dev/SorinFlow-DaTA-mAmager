"use client";

// The look every card on «تماس‌های امروز» shares: a card that animates in,
// and out (sliding toward the reading end) when its work is done.

import { RefreshCw } from "lucide-react";
import { motion } from "motion/react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";

export function QueueItem({ children, className, i = 0 }: { children: React.ReactNode; className?: string; i?: number }) {
  return (
    <motion.li
      layout
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0, transition: { delay: Math.min(i, 8) * 0.04 } }}
      // done: slides out toward the reading end (left in RTL) and collapses
      exit={{ opacity: 0, x: -60, height: 0, marginBottom: 0, paddingTop: 0, paddingBottom: 0, transition: { duration: 0.32 } }}
      className={cn(
        "overflow-hidden rounded-xl border bg-background/60 p-3.5 shadow-xs transition-colors hover:border-primary/30 dark:bg-white/[0.02]",
        className,
      )}
    >
      {children}
    </motion.li>
  );
}

export function RefreshButton({ onClick, spinning, label = "تازه‌سازی" }: { onClick: () => void; spinning: boolean; label?: string }) {
  return (
    <Button variant="ghost" size="icon-sm" onClick={onClick} aria-label={label} disabled={spinning}>
      <RefreshCw className={cn(spinning && "animate-spin")} />
    </Button>
  );
}

export function CountPill({ n }: { n: React.ReactNode }) {
  return <span className="ms-1.5 inline-grid min-w-6 place-items-center rounded-full bg-primary/12 px-1.5 text-[11px] font-bold text-primary tabular">{n}</span>;
}
