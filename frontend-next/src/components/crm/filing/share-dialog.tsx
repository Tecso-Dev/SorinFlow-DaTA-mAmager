"use client";

// GET /filing/files/{id}/share — the customer-safe card: WhatsApp/Telegram
// share links plus «ارسال پیامک». Confidential fields are already stripped
// server-side (app/api/routes/filing.py build_share_card).

import { Copy, Send, Share2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Field, RingDialog } from "@/components/panel/kit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";

type ShareCard = { text: string; serial_no: number | null; images: string[]; removed: string[] };

const REMOVED_FA: Record<string, string> = {
  phone_number: "شماره مالک", seller_name: "نام مالک", owner_phone: "شماره ثبت‌کننده",
  url: "لینک آگهی", address: "آدرس دقیق",
};

export function ShareDialog({ open, onOpenChange, fileId }: { open: boolean; onOpenChange: (o: boolean) => void; fileId: number | null }) {
  const [card, setCard] = useState<ShareCard | null>(null);
  const [text, setText] = useState("");
  const [smsTo, setSmsTo] = useState("");
  const [sending, setSending] = useState(false);

  useEffect(() => {
    if (!open || !fileId) return;
    api<ShareCard>(`/filing/files/${fileId}/share`)
      .then((c) => {
        setCard(c);
        setText(c.text);
      })
      .catch((e) => toast.error("بارگیری کارت ناموفق بود", e instanceof ApiError ? e.message : undefined));
  }, [open, fileId]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("کپی شد", "متن آمادهٔ ارسال است");
    } catch {
      toast.info("کپی خودکار ممکن نشد", "متن را انتخاب و کپی کنید");
    }
  }

  async function sendSms() {
    const digits = smsTo.replace(/\D/g, "");
    if (!/^0?9\d{9}$/.test(digits)) {
      toast.error("شمارهٔ موبایل معتبر نیست");
      return;
    }
    setSending(true);
    try {
      await api("/crm/sms/send", { json: { to_number: digits, message: text } });
      toast.success("پیامک ارسال شد");
      setSmsTo("");
    } catch (e) {
      toast.error("ارسال نشد", e instanceof ApiError ? e.message : undefined);
    } finally {
      setSending(false);
    }
  }

  const enc = encodeURIComponent(text);

  return (
    <RingDialog open={open} onOpenChange={onOpenChange} icon={Share2} wide title="اشتراک‌گذاری با مشتری">
      {!card ? (
        <div className="grid gap-2">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-8 w-full" />
        </div>
      ) : (
        <div className="grid gap-3">
          {card.images.length > 0 && (
            <div className="flex gap-1.5 overflow-x-auto">
              {card.images.slice(0, 6).map((src, i) => (
                // scraped listing photos, arbitrary hosts
                // eslint-disable-next-line @next/next/no-img-element
                <img key={i} src={src} alt="" className="size-20 shrink-0 rounded-lg object-cover" />
              ))}
            </div>
          )}
          <p className="text-xs text-muted-foreground">
            {card.removed.length
              ? `از این متن حذف شد: ${card.removed.map((k) => REMOVED_FA[k] ?? k).join("، ")} — مشتری نمی‌تواند مستقیم با مالک تماس بگیرد.`
              : "اطلاعات محرمانه‌ای برای حذف در این فایل نبود."}
          </p>
          <Field label="متن آماده" htmlFor="share-text">
            <Textarea id="share-text" value={text} onChange={(e) => setText(e.target.value)} rows={7} dir="rtl" />
          </Field>
          <div className="grid grid-cols-3 gap-2">
            <Button variant="outline" asChild>
              <a href={`https://wa.me/?text=${enc}`} target="_blank" rel="noopener noreferrer">واتس‌اپ</a>
            </Button>
            <Button variant="outline" asChild>
              <a href={`https://t.me/share/url?url=&text=${enc}`} target="_blank" rel="noopener noreferrer">تلگرام</a>
            </Button>
            <Button variant="outline" onClick={copy} className="gap-1.5">
              <Copy className="size-4" /> کپی متن
            </Button>
          </div>
          <div className="flex gap-2">
            <Input dir="ltr" inputMode="numeric" placeholder="09123456789" className="tabular" value={smsTo} onChange={(e) => setSmsTo(e.target.value)} aria-label="شمارهٔ گیرندهٔ پیامک" />
            <Button disabled={sending} onClick={sendSms} className="shrink-0 gap-1.5">
              <Send className="size-4" /> ارسال پیامک
            </Button>
          </div>
        </div>
      )}
    </RingDialog>
  );
}
