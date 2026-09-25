// Brand and office settings. Pages never spell the brand themselves: they
// read it from here, and here reads the backend's public site settings
// (root edits them in the panel), falling back to the environment.
// ponytail: env defaults only until milestone 1 wires GET /api/public/site.

export type SiteConfig = {
  brandName: string;
  brandNameLatin: string;
  tagline: string;
  agencyName: string;
  domain: string;
};

export function getSiteConfig(): SiteConfig {
  return {
    brandName: process.env.SITE_BRAND_NAME ?? "سورین‌فلو",
    brandNameLatin: process.env.SITE_BRAND_NAME_LATIN ?? "SorinFlow",
    tagline: process.env.SITE_TAGLINE ?? "CRM املاک و اسکرپر دیوار",
    agencyName: process.env.SITE_AGENCY_NAME ?? "دفتر املاک",
    domain: process.env.SITE_DOMAIN ?? "sorinflow.com",
  };
}
