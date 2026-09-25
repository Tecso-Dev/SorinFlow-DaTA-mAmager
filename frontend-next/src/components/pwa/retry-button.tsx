"use client";

import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";

/** The only thing on the offline page that needs the client: a reload
 * button. Everything else is static so it survives being served from the
 * service worker's cache with no network at all. */
export function RetryButton() {
  return (
    <Button onClick={() => window.location.reload()} className="gap-2">
      <RefreshCw className="size-4" />
      تلاش دوباره
    </Button>
  );
}
