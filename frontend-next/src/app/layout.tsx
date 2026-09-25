import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import { headers } from "next/headers";
import { ThemeProvider } from "@/components/theme-provider";
import { DirectionProvider } from "@/components/ui/direction";
import { TooltipProvider } from "@/components/ui/tooltip";
import { getSiteConfig } from "@/lib/site";
import "./globals.css";

const estedad = localFont({
  src: "./fonts/Estedad-Variable.woff2",
  variable: "--font-estedad",
  weight: "100 900",
  display: "swap",
});

export function generateMetadata(): Metadata {
  const site = getSiteConfig();
  return {
    title: { default: site.brandName, template: `%s — ${site.brandName}` },
    description: site.tagline,
  };
}

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#05050a" },
    { media: "(prefers-color-scheme: light)", color: "#f5f6fb" },
  ],
};

export default async function RootLayout({ children }: LayoutProps<"/">) {
  // proxy.ts puts a fresh nonce on every request; next-themes needs it for
  // the one inline script that sets the theme before first paint.
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  return (
    <html lang="fa" dir="rtl" className={`${estedad.variable} h-full antialiased`} suppressHydrationWarning>
      <body className="min-h-full">
        <ThemeProvider nonce={nonce}>
          <DirectionProvider dir="rtl">
            <TooltipProvider>{children}</TooltipProvider>
          </DirectionProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
