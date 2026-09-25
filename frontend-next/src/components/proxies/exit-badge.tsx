"use client";

// Where a proxy comes out, and whether that helps. «فعال» only means Divar
// answered; the exit country is what decides whether this proxy makes the
// scraper look like a person in Iran or the least convincing thing a
// visitor can be. Say it on the row, not in a tooltip (old panel's
// _proxyExitCell, frontend/js/app.js:6293-6301).

import { ToneBadge } from "@/components/panel/kit";
import type { Proxy } from "./types";

export function ExitBadge({ p }: { p: Proxy }) {
  if (!p.exit_country) {
    return <span className="text-xs text-muted-foreground">— تست نشده</span>;
  }
  const ir = p.exit_country === "IR";
  const kind = p.is_hosting ? "دیتاسنتر" : "خانگی/موبایل";
  const tone = ir && !p.is_hosting ? "success" : ir ? "warning" : "danger";
  return (
    <div className="flex flex-col gap-0.5">
      <span title={p.exit_ip ?? undefined} className="w-fit">
        <ToneBadge tone={tone}>
          {p.exit_country} · {kind}
        </ToneBadge>
      </span>
      {!ir && <span className="text-[11px] text-destructive">برای دیوار مناسب نیست، حتی اگر تست موفق بود</span>}
    </div>
  );
}
