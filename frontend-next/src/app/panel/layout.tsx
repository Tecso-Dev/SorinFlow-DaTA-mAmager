import type { Metadata } from "next";
import { PwaRegister } from "@/components/pwa/register";
import { getSiteConfig } from "@/lib/site";

// Wraps both /panel/login and the authenticated (app) group, so the install
// prompt, the icons below and the service worker (scope /panel/, registered
// here) are available before sign-in too — someone should be able to
// install the app from the login screen.
export async function generateMetadata(): Promise<Metadata> {
  const site = await getSiteConfig();
  return {
    manifest: "/panel/manifest.webmanifest",
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

export default function PanelLayout({ children }: LayoutProps<"/panel">) {
  return (
    <>
      <PwaRegister scope="/panel/" />
      {children}
    </>
  );
}
