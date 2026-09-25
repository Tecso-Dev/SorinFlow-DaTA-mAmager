import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import { headers } from "next/headers";
import { Providers } from "@/components/providers";
import { ThemeProvider } from "@/components/theme-provider";
import { DirectionProvider } from "@/components/ui/direction";
import { TooltipProvider } from "@/components/ui/tooltip";
import appleIcon from "@/components/brand/icons/apple-icon.png";
import icon from "@/components/brand/icons/icon.png";
import { getSiteConfig } from "@/lib/site";
import "./globals.css";

const estedad = localFont({
  src: "./fonts/Estedad-Variable.woff2",
  variable: "--font-estedad",
  weight: "100 900",
  display: "swap",
});

export async function generateMetadata(): Promise<Metadata> {
  const site = await getSiteConfig();
  return {
    title: { default: site.seoTitle || site.brandName, template: `%s — ${site.brandName}` },
    description: site.seoDescription || site.tagline,
    // Imported, so they are served from /_next/static (drawn from the logo by
    // scripts/brand-assets.mjs); /favicon.ico stays the file-convention one.
    icons: { icon: [{ url: icon.src, type: "image/png", sizes: "512x512" }], apple: [{ url: appleIcon.src, sizes: "180x180" }] },
  };
}

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#05050a" },
    { media: "(prefers-color-scheme: light)", color: "#f5f6fb" },
  ],
};

export default async function RootLayout({ children }: LayoutProps<"/">) {
  // proxy.ts puts a fresh nonce on every request. Next.js stamps it on its own
  // scripts; the few libraries that add a <style> at runtime get it here.
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  return (
    <html lang="fa" dir="rtl" className={`${estedad.variable} h-full antialiased`} suppressHydrationWarning>
      <body className="min-h-full">
        <ThemeProvider nonce={nonce}>
          <DirectionProvider dir="rtl">
            <TooltipProvider>
              <Providers nonce={nonce}>{children}</Providers>
            </TooltipProvider>
          </DirectionProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
