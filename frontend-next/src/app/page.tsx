import type { Metadata } from "next";
import { headers } from "next/headers";
import { Landing, type LandingStats } from "@/components/landing/landing";
import { fill, LANDING } from "@/content/landing";
import { faNum } from "@/lib/format";
import { BACKEND, getSiteConfig, type SiteConfig } from "@/lib/site";

// The public landing page: rendered on the server with the brand, contact
// details and live numbers already in the HTML, so search engines and link
// previews (Telegram, WhatsApp) see the real page.

const siteUrl = (site: SiteConfig) => `https://${site.domain}`;

export async function generateMetadata(): Promise<Metadata> {
  const site = await getSiteConfig();
  const brand = site.brandName;
  const title = site.seoTitle || fill(LANDING.meta.title, brand);
  const description = site.seoDescription || fill(LANDING.meta.description, brand);
  const og = { url: "/og.png", width: 1200, height: 630, alt: fill(LANDING.meta.ogAlt, brand), type: "image/png" };
  return {
    metadataBase: new URL(siteUrl(site)),
    title: { absolute: title },
    description,
    alternates: { canonical: "/" },
    robots: { index: true, follow: true, "max-snippet": -1, "max-image-preview": "large" },
    openGraph: {
      type: "website",
      siteName: brand,
      locale: "fa_IR",
      url: "/",
      title,
      description: fill(LANDING.meta.ogDescription, brand),
      images: [og],
    },
    twitter: { card: "summary_large_image", title, description: fill(LANDING.meta.ogDescription, brand), images: [og] },
  };
}

async function getJson<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${BACKEND}${path}`, { next: { revalidate: 60 }, signal: AbortSignal.timeout(1500) });
    return res.ok ? ((await res.json()) as T) : null;
  } catch {
    return null;
  }
}

/** schema.org description of the product, built from the settings. */
function jsonLd(site: SiteConfig) {
  const url = `${siteUrl(site)}/`;
  const brand = site.brandName;
  const org: Record<string, unknown> = {
    "@type": "Organization",
    "@id": `${url}#org`,
    name: brand,
    alternateName: site.brandNameLatin || undefined,
    url,
    logo: `${url}icon.png`,
    email: site.email || undefined,
    telephone: site.phone || undefined,
  };
  if (site.telegram) org.sameAs = [site.telegram.startsWith("http") ? site.telegram : `https://t.me/${site.telegram.replace(/^@/, "")}`];
  return {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "SoftwareApplication",
        "@id": `${url}#app`,
        name: brand,
        alternateName: site.brandNameLatin || undefined,
        applicationCategory: "BusinessApplication",
        applicationSubCategory: "Real Estate CRM",
        operatingSystem: "Web",
        inLanguage: "fa-IR",
        url,
        description: site.seoDescription || fill(LANDING.meta.description, brand),
        featureList: LANDING.featureList,
        publisher: { "@id": `${url}#org` },
      },
      org,
      { "@type": "WebSite", "@id": `${url}#site`, url, name: brand, inLanguage: "fa-IR", publisher: { "@id": `${url}#org` } },
      {
        "@type": "FAQPage",
        "@id": `${url}#faq`,
        mainEntity: LANDING.faq.map((f) => ({
          "@type": "Question",
          name: fill(f.q, brand),
          acceptedAnswer: { "@type": "Answer", text: fill(f.a, brand) },
        })),
      },
    ],
  };
}

export default async function Home() {
  const [site, stats, auth, nonce] = await Promise.all([
    getSiteConfig(),
    getJson<NonNullable<LandingStats>>("/api/public/stats"),
    getJson<{ enabled: boolean }>("/api/public/auth/status"),
    headers().then((h) => h.get("x-nonce") ?? undefined),
  ]);
  // JSON inside a data block: escape "<" so no string can close the tag.
  const ld = JSON.stringify(jsonLd(site)).replace(/</g, "\\u003c");
  return (
    <>
      {/* A data block, never executed; it still carries the nonce so the CSP stays strict. */}
      <script type="application/ld+json" nonce={nonce} dangerouslySetInnerHTML={{ __html: ld }} />
      <Landing
        site={{
          brandName: site.brandName,
          tagline: site.tagline,
          domain: site.domain,
          phone: site.phone,
          email: site.email,
          telegram: site.telegram,
        }}
        stats={stats}
        portalOpen={!!auth?.enabled}
        year={faNum(new Date().getFullYear(), { useGrouping: false })}
      />
    </>
  );
}
