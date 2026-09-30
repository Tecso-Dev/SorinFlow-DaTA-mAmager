import type { MetadataRoute } from "next";
import { BACKEND, getSiteConfig } from "@/lib/site";

// The public pages: the landing page, and the customer portal while public
// sign-up is on (otherwise /portal only redirects).
export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const { domain } = await getSiteConfig();
  const base = `https://${domain}`;
  let portal = false;
  try {
    const res = await fetch(`${BACKEND}/api/public/auth/status`, { next: { revalidate: 300 }, signal: AbortSignal.timeout(1500) });
    portal = res.ok && !!((await res.json()) as { enabled?: boolean }).enabled;
  } catch {
    portal = false;
  }
  return [
    { url: `${base}/`, changeFrequency: "weekly", priority: 1 },
    ...(portal ? [{ url: `${base}/portal`, changeFrequency: "monthly" as const, priority: 0.6 }] : []),
  ];
}
