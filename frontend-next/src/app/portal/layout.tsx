import type { Metadata } from "next";
import { PortalConfirmShell } from "@/components/portal/confirm-shell";
import { PwaRegister } from "@/components/pwa/register";
import { getSiteConfig } from "@/lib/site";

// The public portal's own root: no AppShell (that is the staff panel's), just
// the confirm dialog every signed-in portal page needs (deleting a request)
// — mounted once here rather than per page, the same way the panel mounts it
// once in its own shell — plus the PWA scaffolding (manifest, icons, the
// /portal/ scope worker) that mirrors panel/layout.tsx. Scaffolding only:
// the portal itself is still frontend/portal.html until Phase 4 step 8.
// generateMetadata makes this a server component, so the confirm dialog
// (a client component needing the icon prop) is a separate client wrapper
// — see the comment in confirm-shell.tsx.
export async function generateMetadata(): Promise<Metadata> {
  const site = await getSiteConfig();
  return {
    manifest: "/portal/manifest.webmanifest",
    appleWebApp: {
      capable: true,
      title: site.brandNameLatin || site.brandName,
      statusBarStyle: "black-translucent",
    },
    icons: {
      icon: [{ url: "/icons/favicon-32.png", sizes: "32x32", type: "image/png" }],
      apple: [{ url: "/icons/apple-touch-icon.png", sizes: "180x180", type: "image/png" }],
    },
  };
}

export default function PortalLayout({ children }: LayoutProps<"/portal">) {
  return (
    <div className="min-h-dvh bg-background text-foreground">
      <PwaRegister scope="/portal/" />
      <PortalConfirmShell>{children}</PortalConfirmShell>
    </div>
  );
}
