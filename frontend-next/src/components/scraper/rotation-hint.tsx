"use client";

// «چرخش شماره دیوار»: how many ads before switching accounts — a BIGGER
// number rotates LESS and leans harder on one number, which is what
// triggers Divar's SMS-OTP. The direction is unintuitive on purpose (the
// old panel's own comment says so), so the hint spells it out every time.

import { faNum } from "@/lib/format";

export function RotationHint({ n, validSessions }: { n: number | undefined; validSessions: number }) {
  const lines: { text: string; tone: "danger" | "success" | "warning" | "muted" }[] = [];

  if (validSessions <= 1) {
    lines.push({
      tone: "danger",
      text: `با ${faNum(validSessions)} حساب فعال، چرخش هیچ کاری نمی‌کند — هر عددی بگذارید فرقی ندارد. برای اینکه کار کند، در «احراز هویت دیوار» حساب دوم اضافه کنید.`,
    });
  } else {
    lines.push({ tone: "success", text: `${faNum(validSessions)} حساب فعال دارید، پس چرخش کار می‌کند.` });
  }

  if (n !== undefined) {
    if (n === 0) {
      lines.push({ tone: "warning", text: "۰ یعنی بدون چرخش — همهٔ بار روی یک شماره، بیشترین پیامک." });
    } else if (n > 150) {
      lines.push({ tone: "warning", text: `هر ${faNum(n)} آگهی یک بار سوییچ می‌کند — چرخش خیلی کم. اگر پیامک احراز هویت زیاد شد، عدد را پایین بیاورید نه بالا.` });
    } else if (n >= 40) {
      lines.push({ tone: "muted", text: `هر ${faNum(n)} آگهی سوییچ می‌کند — هر شماره ${faNum(n)} درخواست پشت‌سرهم می‌دهد. اگر دیوار زیاد کد خواست، عدد را پایین بیاورید.` });
    } else if (n <= 10) {
      lines.push({ tone: "muted", text: `هر ${faNum(n)} آگهی سوییچ می‌کند — کمترین پیامک، ولی هر سوییچ چند ثانیه به هر اسکرپ اضافه می‌کند.` });
    }
  }

  const TONE = { danger: "text-destructive", success: "text-success", warning: "text-warning", muted: "text-muted-foreground" };
  return (
    <div className="grid gap-0.5 text-xs leading-6">
      {lines.map((l, i) => (
        <span key={i} className={TONE[l.tone]}>{l.text}</span>
      ))}
    </div>
  );
}
