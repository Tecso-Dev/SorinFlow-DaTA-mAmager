import { notFound } from "next/navigation";
import { Dashboard } from "@/components/preview/dashboard";
import { PreviewShell, type Variant } from "@/components/preview/shell";
import { PreviewSwitcher } from "@/components/preview/switcher";
import { getSiteConfig } from "@/lib/site";

const VARIANTS: Variant[] = ["a", "b", "c"];

export const metadata = { title: "پیشنهاد ظاهر", robots: { index: false } };

export default async function PreviewPage(props: PageProps<"/preview/[variant]">) {
  const { variant } = await props.params;
  const { theme } = await props.searchParams;
  if (!VARIANTS.includes(variant as Variant)) notFound();
  const v = variant as Variant;
  const t = theme === "light" || theme === "dark" ? theme : undefined;

  return (
    <div data-variant={v} className="min-h-dvh bg-background text-foreground">
      <PreviewShell variant={v} site={getSiteConfig()}>
        <Dashboard variant={v} />
      </PreviewShell>
      <PreviewSwitcher current={v} theme={t} />
    </div>
  );
}
