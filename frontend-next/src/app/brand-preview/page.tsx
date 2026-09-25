import { BrandGallery } from "@/components/brand/gallery";
import { getSiteConfig } from "@/lib/site";

export const metadata = { title: "پیش‌نمایش لوگو", robots: { index: false, follow: false } };

export default async function BrandPreviewPage() {
  const { brandName, brandNameLatin, tagline, domain } = await getSiteConfig();
  return <BrandGallery site={{ brandName, brandNameLatin, tagline, domain }} />;
}
