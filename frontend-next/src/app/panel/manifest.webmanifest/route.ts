import { NextResponse } from "next/server";
import { getSiteConfig } from "@/lib/site";

// Next's manifest.ts file convention only resolves at the app root (see
// node_modules/next/dist/lib/metadata/is-metadata-route.js — its regex is
// anchored, unlike icon.tsx's), so two scoped manifests (this one and
// ../../portal/manifest.webmanifest/route.ts) need a plain route handler
// instead, named for the literal URL Next.js's own docs ask a manifest to
// be served at. src/app/panel/layout.tsx links it explicitly since the
// auto <link rel="manifest"> only fires for the special file convention.
export async function GET() {
  const site = await getSiteConfig();
  const manifest = {
    id: "/panel",
    name: site.brandName,
    short_name: site.brandNameLatin || site.brandName,
    description: site.tagline || site.seoDescription,
    start_url: "/panel",
    scope: "/panel/",
    display: "standalone",
    orientation: "portrait-primary",
    // «شب نیلی»: the same dark tokens globals.css declares for .dark.
    background_color: "#05050a",
    theme_color: "#05050a",
    dir: "rtl",
    lang: "fa",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
    shortcuts: [
      { name: "داشبورد", url: "/panel", icons: [{ src: "/icons/icon-192.png", sizes: "192x192" }] },
      { name: "CRM و لیدها", url: "/panel/crm", icons: [{ src: "/icons/icon-192.png", sizes: "192x192" }] },
      { name: "لیست املاک", url: "/panel/properties", icons: [{ src: "/icons/icon-192.png", sizes: "192x192" }] },
    ],
  };
  return NextResponse.json(manifest, { headers: { "Content-Type": "application/manifest+json" } });
}
