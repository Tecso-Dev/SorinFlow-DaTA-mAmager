"use client";

// The setup QR carries the device's live secret (app/api/routes/forwarder.py
// device_config — it is built fresh on every request from the row), so it
// sits behind a shield by default and hides itself again after 30 seconds.
// Rendered as a data: URI <img> from the `qrcode` npm package — never a
// <script> or a remote fetch, so the strict CSP (img-src allows data:) never
// sees it.

import { Copy, Eye, EyeOff, ShieldCheck } from "lucide-react";
import { motion } from "motion/react";
import QRCode from "qrcode";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/toaster";
import { faNum } from "@/lib/format";

const REVEAL_SECONDS = 30;

export function QrShield({ payload }: { payload: string }) {
  const [revealed, setRevealed] = useState(false);
  const [dataUrl, setDataUrl] = useState<string | null>(null);
  const [left, setLeft] = useState(REVEAL_SECONDS);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const tick = useRef<ReturnType<typeof setInterval> | null>(null);

  const clearTimers = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    if (tick.current) clearInterval(tick.current);
    timer.current = null;
    tick.current = null;
  }, []);

  const hide = useCallback(() => {
    clearTimers();
    setRevealed(false);
    setDataUrl(null);
  }, [clearTimers]);

  function reveal() {
    clearTimers();
    setLeft(REVEAL_SECONDS);
    setRevealed(true);
    timer.current = setTimeout(hide, REVEAL_SECONDS * 1000);
    tick.current = setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000);
  }

  useEffect(() => clearTimers, [clearTimers]);

  // Generated only while revealed, and regenerated whenever the payload
  // itself changes under us (a rotate or a SIM edit while the guide stays
  // open) — the QR never shows a stale secret.
  useEffect(() => {
    if (!revealed) return;
    let alive = true;
    QRCode.toDataURL(payload, { margin: 1, width: 220 })
      .then((url) => alive && setDataUrl(url))
      .catch(() => alive && setDataUrl(null));
    return () => {
      alive = false;
    };
  }, [revealed, payload]);

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(payload);
      toast.success("لینک راه‌اندازی کپی شد");
    } catch {
      toast.error("کپی نشد", "دسترسی به کلیپ‌بورد رد شد");
    }
  }

  return (
    <div className="flex flex-col items-center gap-3">
      <div className="relative grid size-[210px] place-items-center overflow-hidden rounded-2xl border bg-muted/30">
        {/* Plain conditional rendering, not AnimatePresence: this swap is a
            real security state (the secret's visibility), so hiding it must
            take effect the instant `revealed` goes false, never wait for an
            exit animation to report itself finished. Each branch still
            animates in on its own mount. */}
        {revealed ? (
          <motion.div
            key="qr"
            initial={{ opacity: 0, scale: 0.94 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.22 }}
            className="flex flex-col items-center gap-2"
          >
            {dataUrl ? (
              // eslint-disable-next-line @next/next/no-img-element -- a data: URI, not a remote image
              <img src={dataUrl} alt="کد QR راه‌اندازی؛ رمز این دستگاه را دارد" width={180} height={180} className="rounded-lg bg-white p-2" />
            ) : (
              <div className="size-[180px] animate-pulse rounded-lg bg-muted" aria-hidden />
            )}
            <button type="button" onClick={hide} className="flex items-center gap-1 text-[11px] text-muted-foreground outline-none hover:text-foreground focus-visible:text-foreground">
              <EyeOff className="size-3" aria-hidden />
              پنهان کردن ({faNum(left)} ثانیه تا پنهان‌شدن خودکار)
            </button>
          </motion.div>
        ) : (
          <motion.button
            key="shield"
            type="button"
            onClick={reveal}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="relative flex flex-col items-center gap-2.5 rounded-xl px-4 py-3 outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
            aria-label="نمایش کد QR راه‌اندازی، تا ۳۰ ثانیه"
          >
            <span className="relative grid size-16 place-items-center">
              <motion.span
                aria-hidden
                className="absolute inset-0 rounded-full"
                style={{ background: "conic-gradient(from 0deg, var(--primary), transparent 65%, var(--primary))" }}
                animate={{ rotate: 360 }}
                transition={{ duration: 5, repeat: Infinity, ease: "linear" }}
              />
              <span className="absolute inset-[3px] rounded-full bg-card" aria-hidden />
              <ShieldCheck className="relative size-7 text-primary" aria-hidden />
            </span>
            <span className="text-xs font-semibold text-foreground">نمایش کد QR</span>
            <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
              <Eye className="size-3" aria-hidden /> رمز دستگاه را دارد؛ فقط ۳۰ ثانیه نمایان می‌ماند
            </span>
          </motion.button>
        )}
      </div>
      <Button type="button" variant="outline" size="sm" onClick={copyLink}>
        <Copy /> کپی لینک راه‌اندازی
      </Button>
    </div>
  );
}
