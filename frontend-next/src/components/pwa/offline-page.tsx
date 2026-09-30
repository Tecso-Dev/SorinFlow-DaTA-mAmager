"use client";

import { WifiOff } from "lucide-react";
import { IsoBadge } from "@/components/panel/kit";
import { RetryButton } from "./retry-button";

/** Shared body for /panel/offline and /portal/offline — precached by
 * public/sw.js so a page navigation with no network still lands somewhere
 * legible instead of the browser's own offline error. No session, no API
 * call, nothing that could itself need the network. "use client" only
 * because IsoBadge takes its icon as a component reference, which a server
 * component cannot pass across the boundary. */
export function OfflinePage({ homeLabel, homeHref }: { homeLabel: string; homeHref: string }) {
  return (
    <div className="relative flex min-h-dvh items-center justify-center overflow-hidden bg-background px-6">
      <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -top-40 start-[15%] size-[420px] rounded-full bg-(--glow-1) blur-3xl" />
        <div className="absolute -bottom-32 -end-24 size-[360px] rounded-full bg-(--glow-2) blur-3xl" />
      </div>
      <div className="relative flex max-w-sm flex-col items-center gap-5 text-center">
        <IsoBadge icon={WifiOff} className="size-20 [&_svg]:size-9" />
        <div className="space-y-2">
          <h1 className="text-xl font-black tracking-tight">به اینترنت وصل نیستید</h1>
          <p className="text-sm leading-7 text-muted-foreground">
            این صفحه از حافظهٔ همین دستگاه نشان داده می‌شود. با وصل‌شدن دوباره به اینترنت،
            بقیهٔ صفحه‌ها هم مثل همیشه در دسترس‌اند.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <RetryButton />
          <a
            href={homeHref}
            className="rounded-md px-4 py-2 text-sm font-medium text-muted-foreground outline-none transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          >
            {homeLabel}
          </a>
        </div>
      </div>
    </div>
  );
}
