import type { Metadata } from "next";
import { OfflinePage } from "@/components/pwa/offline-page";

// Scaffolding only: the portal itself is still frontend/portal.html
// (Phase 4 step 8 rebuilds it here). This page and its manifest
// (../manifest.ts) are prepared so the PWA setup needs no changes once
// the portal's real routes land — see docs/phase4/reports/p4-r5-pwa-email.md.
export const metadata: Metadata = { title: "بدون اینترنت" };

export default function PortalOffline() {
  return <OfflinePage homeLabel="بازگشت به پورتال" homeHref="/portal" />;
}
