import type { MetadataRoute } from "next";
import { getSiteConfig } from "@/lib/site";

// Only the landing page is for search engines; the panel, the API and the
// logo gallery are not.
export default async function robots(): Promise<MetadataRoute.Robots> {
  const { domain } = await getSiteConfig();
  return {
    rules: { userAgent: "*", allow: "/", disallow: ["/panel", "/api/", "/dashboard", "/brand-preview"] },
    sitemap: `https://${domain}/sitemap.xml`,
    host: `https://${domain}`,
  };
}
