import { PortalDashboard } from "@/components/portal/dashboard";
import { getSiteConfig } from "@/lib/site";

export const metadata = { title: "درخواست‌های من", robots: { index: false, follow: false } };

export default async function PortalMePage() {
  const { brandName, brandNameLatin } = await getSiteConfig();
  return <PortalDashboard site={{ brandName, brandNameLatin }} />;
}
