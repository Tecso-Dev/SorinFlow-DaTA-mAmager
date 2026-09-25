import type { Metadata } from "next";
import { PwaRegister } from "@/components/pwa/register";
import { getSiteConfig } from "@/lib/site";

// Scaffolding, like manifest.ts and offline/page.tsx in this same
// directory: the portal itself is still frontend/portal.html (Phase 4 step
// 8 rebuilds it here). Registers the /portal/ scope worker for whatever
// lands under this segment meanwhile — today, only the offline page.
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
    <>
      <PwaRegister scope="/portal/" />
      {children}
    </>
  );
}
