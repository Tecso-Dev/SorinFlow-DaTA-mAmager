"use client";

// «بدون ورود به دیوار»: shown before a run starts on a number with no
// valid, logged-in session. The run can still go ahead — a lot of Divar is
// visible logged out — but the phone numbers on the ads will not be.

import { TriangleAlert } from "lucide-react";
import Link from "next/link";
import { RingDialog } from "@/components/panel/kit";
import { Button } from "@/components/ui/button";

export function CookieWarningDialog({
  open, onOpenChange, expired, onContinue,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  expired: boolean;
  onContinue: () => void;
}) {
  return (
    <RingDialog
      open={open}
      onOpenChange={onOpenChange}
      icon={TriangleAlert}
      tone="danger"
      title="هشدار: بدون ورود به دیوار"
      description={expired ? "نشست دیوار شما منقضی شده است." : "شما هنوز وارد حساب دیوار نشده‌اید."}
    >
      <div className="grid gap-1.5 rounded-lg bg-muted/50 p-3 text-xs leading-6 text-muted-foreground">
        <p className="font-semibold text-foreground">بدون ورود به حساب:</p>
        <ul className="list-inside list-disc">
          <li>شماره تماس مالکان نمایش داده نمی‌شود</li>
          <li>برخی اطلاعات ممکن است در دسترس نباشد</li>
          <li>ممکن است با محدودیت‌های دیوار مواجه شوید</li>
        </ul>
      </div>
      <div className="mt-3 grid gap-2">
        <Button
          variant="outline"
          className="w-full"
          onClick={() => {
            onContinue();
            onOpenChange(false);
          }}
        >
          ادامه بدون ورود
        </Button>
        <Button variant="ghost" className="w-full" asChild>
          <Link href="/panel/divar">ورود به حساب</Link>
        </Button>
      </div>
    </RingDialog>
  );
}
