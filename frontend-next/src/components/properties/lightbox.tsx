"use client";

// The property gallery: a carousel of thumbnails, and a full-screen viewer
// that zooms (wheel, double-click/tap, +/−), pans when zoomed (drag), and
// steps through the photos (arrows, keys). Radix Dialog for the focus trap
// and Esc — there is no existing lightbox to reuse from this stream's area,
// so this is a purpose-built one.

import { ChevronLeft, ChevronRight, ImageOff, Minus, Plus, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { Dialog as D } from "radix-ui";
import { useRef, useState } from "react";
import { cn } from "cn";
import { faNum } from "@/lib/format";
import { safeImg } from "./shared";

/** A row of thumbnails; click/tap any one to open the full-screen viewer on it. */
export function PhotoCarousel({ images }: { images: string[] | null | undefined }) {
  const list = (images ?? []).map(safeImg).filter((s): s is string => !!s);
  const [open, setOpen] = useState<number | null>(null);
  if (!list.length) {
    return (
      <div className="flex h-40 items-center justify-center rounded-xl border border-dashed text-sm text-muted-foreground">
        <ImageOff className="me-1.5 size-4" aria-hidden /> بدون تصویر
      </div>
    );
  }
  return (
    <>
      <div className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1 [scrollbar-width:thin]">
        {list.map((src, i) => (
          <motion.button
            key={src + i}
            type="button"
            onClick={() => setOpen(i)}
            whileHover={{ y: -3, rotateX: 6 }}
            transition={{ type: "spring", stiffness: 300, damping: 20 }}
            style={{ transformPerspective: 600 }}
            className="relative size-24 shrink-0 cursor-zoom-in overflow-hidden rounded-xl border bg-muted shadow-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label={`بزرگ کردن تصویر ${faNum(i + 1)}`}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={src} alt="" loading="lazy" className="size-full object-cover" />
          </motion.button>
        ))}
      </div>
      <p className="mt-1.5 text-xs text-muted-foreground">{faNum(list.length)} تصویر</p>
      <Lightbox images={list} index={open} onIndex={setOpen} />
    </>
  );
}

export function Lightbox({ images, index, onIndex }: { images: string[]; index: number | null; onIndex: (i: number | null) => void }) {
  const [scale, setScale] = useState(1);
  const [pos, setPos] = useState({ x: 0, y: 0 });
  const [broken, setBroken] = useState(false);
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(null);
  const open = index !== null;
  const n = images.length;

  function reset() {
    setScale(1);
    setPos({ x: 0, y: 0 });
    setBroken(false);
  }
  function go(step: number) {
    if (index === null || n < 2) return;
    reset();
    onIndex((index + step + n) % n);
  }
  function zoom(f: number) {
    setScale((s) => {
      const next = Math.min(8, Math.max(1, s * f));
      if (next === 1) setPos({ x: 0, y: 0 });
      return next;
    });
  }

  return (
    <D.Root open={open} onOpenChange={(o) => { if (!o) { reset(); onIndex(null); } }}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-[60] bg-black/85 backdrop-blur-sm data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
        <D.Content
          className="fixed inset-0 z-[60] flex items-center justify-center outline-none"
          onKeyDown={(e) => {
            // RTL: the right arrow is «previous»
            if (e.key === "ArrowRight") go(-1);
            if (e.key === "ArrowLeft") go(1);
            if (e.key === "+" || e.key === "=") zoom(1.3);
            if (e.key === "-") zoom(1 / 1.3);
          }}
        >
          <D.Title className="sr-only">تصویر {faNum((index ?? 0) + 1)} از {faNum(n)}</D.Title>
          <D.Description className="sr-only">با چرخ موس یا دو بار زدن بزرگ کنید؛ با کشیدن جابه‌جا کنید.</D.Description>
          <div
            className={cn("relative grid size-full touch-none place-items-center overflow-hidden", scale > 1 ? "cursor-grab active:cursor-grabbing" : "cursor-zoom-in")}
            onWheel={(e) => zoom(e.deltaY < 0 ? 1.2 : 1 / 1.2)}
            onDoubleClick={() => (scale > 1 ? reset() : setScale(2.5))}
            onPointerDown={(e) => {
              if (scale <= 1) return;
              (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
              drag.current = { x: e.clientX, y: e.clientY, px: pos.x, py: pos.y };
            }}
            onPointerMove={(e) => {
              const d = drag.current;
              if (!d) return;
              setPos({ x: d.px + (e.clientX - d.x) / scale, y: d.py + (e.clientY - d.y) / scale });
            }}
            onPointerUp={() => (drag.current = null)}
            onPointerCancel={() => (drag.current = null)}
          >
            <AnimatePresence mode="wait">
              {index !== null && (
                <motion.div
                  key={index}
                  initial={{ opacity: 0, scale: 0.94 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.97 }}
                  transition={{ duration: 0.2 }}
                  className="grid place-items-center"
                >
                  {broken ? (
                    <div className="flex flex-col items-center gap-2 text-sm text-white/70">
                      <ImageOff className="size-8" /> تصویر باز نشد
                    </div>
                  ) : (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={images[index]}
                      alt={`تصویر ${faNum(index + 1)}`}
                      draggable={false}
                      onError={() => setBroken(true)}
                      className="max-h-[88dvh] max-w-[94vw] select-none rounded-lg object-contain shadow-2xl transition-transform duration-150"
                      style={{ transform: `scale(${scale}) translate(${pos.x}px, ${pos.y}px)` }}
                    />
                  )}
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          <div className="absolute inset-x-0 top-0 flex items-center justify-between gap-2 p-3 text-white">
            <span className="rounded-full bg-white/10 px-3 py-1 text-xs tabular">
              {faNum((index ?? 0) + 1)} / {faNum(n)}
            </span>
            <div className="flex items-center gap-1">
              <LbButton label="کوچک‌تر" onClick={() => zoom(1 / 1.3)} disabled={scale <= 1}><Minus className="size-4" /></LbButton>
              <span className="w-12 text-center text-xs tabular">{faNum(Math.round(scale * 100))}٪</span>
              <LbButton label="بزرگ‌تر" onClick={() => zoom(1.3)} disabled={scale >= 8}><Plus className="size-4" /></LbButton>
              <D.Close asChild>
                <LbButton label="بستن"><X className="size-4" /></LbButton>
              </D.Close>
            </div>
          </div>
          {n > 1 && (
            <>
              <LbButton label="تصویر قبل" className="absolute start-3 top-1/2 -translate-y-1/2" onClick={() => go(-1)}>
                <ChevronRight className="size-5" />
              </LbButton>
              <LbButton label="تصویر بعد" className="absolute end-3 top-1/2 -translate-y-1/2" onClick={() => go(1)}>
                <ChevronLeft className="size-5" />
              </LbButton>
            </>
          )}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}

function LbButton({ label, className, children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      {...props}
      className={cn(
        "grid size-10 place-items-center rounded-full bg-white/10 text-white outline-none backdrop-blur transition hover:bg-white/20 focus-visible:ring-2 focus-visible:ring-white disabled:opacity-40",
        className,
      )}
    >
      {children}
    </button>
  );
}
