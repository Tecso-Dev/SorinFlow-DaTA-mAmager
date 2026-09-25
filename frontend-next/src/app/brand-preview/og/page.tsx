import { OgCard } from "@/components/brand/assets";
import { getSiteConfig } from "@/lib/site";

// The 1200×630 link-preview card, drawn with the live logo and the brand from
// settings. scripts/brand-assets.mjs screenshots it into public/og.png.
export const metadata = { title: "OG", robots: { index: false, follow: false } };

export default async function OgPage() {
  const { brandName, brandNameLatin, tagline } = await getSiteConfig();
  return <OgCard brandName={brandName} brandNameLatin={brandNameLatin} tagline={tagline} />;
}
