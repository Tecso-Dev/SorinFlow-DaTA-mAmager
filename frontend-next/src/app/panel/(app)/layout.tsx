import { AppShell } from "@/components/panel/app-shell";
import { getSiteConfig } from "@/lib/site";

export const metadata = { robots: { index: false, follow: false } };

export default async function PanelLayout({ children }: LayoutProps<"/panel">) {
  const { brandName, brandNameLatin, tagline } = await getSiteConfig();
  return <AppShell site={{ brandName, brandNameLatin, tagline }}>{children}</AppShell>;
}
