"use client";

import { CircleHelp } from "lucide-react";
import { ConfirmProvider } from "@/components/panel/kit";

// A function (the icon component) cannot cross the server/client boundary as
// a prop — only the panel's AppShell (itself "use client") gets away with
// `<ConfirmProvider fallbackIcon={CircleHelp}>` inline. portal/layout.tsx is
// a server component (it exports generateMetadata), so it renders this
// client wrapper instead of passing the icon down itself.
export function PortalConfirmShell({ children }: { children: React.ReactNode }) {
  return <ConfirmProvider fallbackIcon={CircleHelp}>{children}</ConfirmProvider>;
}
