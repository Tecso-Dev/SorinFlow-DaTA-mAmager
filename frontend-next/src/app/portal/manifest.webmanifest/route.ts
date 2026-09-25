import { NextResponse } from "next/server";
import { getSiteConfig } from "@/lib/site";

// See ../../panel/manifest.webmanifest/route.ts for why this is a route
// handler and not the manifest.ts file convention. Scaffolding, like the
// rest of this segment: the portal itself is still frontend/portal.html.
export async function GET() {
  const site = await getSiteConfig();
  const manifest = {
    id: "/portal",
    name: `${site.brandName} — پورتال مشتریان`,
    short_name: site.brandNameLatin || site.brandName,
    description: site.tagline || site.seoDescription,
    start_url: "/portal",
    scope: "/portal/",
    display: "standalone",
    orientation: "portrait-primary",
    background_color: "#05050a",
    theme_color: "#05050a",
    dir: "rtl",
    lang: "fa",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
  return NextResponse.json(manifest, { headers: { "Content-Type": "application/manifest+json" } });
}
