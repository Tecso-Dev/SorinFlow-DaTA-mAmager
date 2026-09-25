"use client";

import { useEffect } from "react";

/**
 * Registers public/sw.js for one scope. Mounted once per app — the panel's
 * src/app/panel/layout.tsx with scope="/panel/", and the portal's own
 * layout once it exists with scope="/portal/" (see
 * src/app/portal/manifest.ts and .../offline/page.tsx, prepared ahead of
 * that). Production only: a service worker caching a dev server's ever-
 * changing bundle is a debugging trap, not a feature.
 */
export function PwaRegister({ scope }: { scope: string }) {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production") return;
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js", { scope }).catch(() => {
      // Best-effort: a page must work with no offline fallback rather than
      // fail because the worker could not be installed.
    });
  }, [scope]);

  return null;
}
