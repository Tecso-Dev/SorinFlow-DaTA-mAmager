import "server-only";

// Brand and office settings. Pages never spell the brand themselves: root
// edits it in the panel (GET /api/public/site serves it), and until the
// backend answers, the environment's defaults stand in.

export type SiteConfig = {
  brandName: string;
  brandNameLatin: string;
  tagline: string;
  agencyName: string;
  domain: string;
  phone: string;
  email: string;
  telegram: string;
  instagram: string;
  address: string;
  seoTitle: string;
  seoDescription: string;
};

const fallback = (): SiteConfig => ({
  brandName: process.env.SITE_BRAND_NAME ?? "سورین‌فلو",
  brandNameLatin: process.env.SITE_BRAND_NAME_LATIN ?? "SorinFlow",
  tagline: process.env.SITE_TAGLINE ?? "CRM املاک و اسکرپر دیوار",
  agencyName: process.env.SITE_AGENCY_NAME ?? "",
  domain: process.env.SITE_DOMAIN ?? "sorinflow.com",
  phone: "",
  email: "",
  telegram: "",
  instagram: "",
  address: "",
  seoTitle: "",
  seoDescription: "",
});

// Fields that may never be blank; the rest (phone, email, ...) may be cleared.
const REQUIRED = new Set<keyof SiteConfig>(["brandName", "brandNameLatin", "domain"]);

/** Backend base for server-side calls: the k8s Service in production. */
export const BACKEND = process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8020";

export async function getSiteConfig(): Promise<SiteConfig> {
  try {
    const res = await fetch(`${BACKEND}/api/public/site`, {
      next: { revalidate: 60 },
      signal: AbortSignal.timeout(1500),
    });
    if (!res.ok) return fallback();
    // An empty or missing field keeps its default (an empty domain would
    // otherwise make "https://" URLs and break the landing page's metadata).
    const got = (await res.json()) as Record<string, unknown>;
    const base = fallback();
    for (const k of Object.keys(base) as (keyof SiteConfig)[]) {
      const v = got[k];
      if (typeof v === "string" && (v.trim() || !REQUIRED.has(k))) base[k] = v.trim();
    }
    return base;
  } catch {
    return fallback();
  }
}
