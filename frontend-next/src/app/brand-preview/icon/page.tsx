import { IconArt } from "@/components/brand/assets";

// The app icons, drawn from the current logo: ?kind=favicon (transparent),
// apple (opaque, for iOS) or maskable (art inside the 80% safe zone).
// scripts/brand-assets.mjs screenshots them into src/app/*.png and favicon.ico.
export const metadata = { title: "Icon", robots: { index: false, follow: false } };

export default async function IconPage({ searchParams }: PageProps<"/brand-preview/icon">) {
  const { kind } = await searchParams;
  const k = kind === "apple" || kind === "maskable" ? kind : "favicon";
  return <IconArt kind={k} />;
}
