"use client";

import { ThemeProvider as NextThemes } from "next-themes";

export function ThemeProvider({ nonce, children }: { nonce?: string; children: React.ReactNode }) {
  return (
    <NextThemes
      attribute="class"
      defaultTheme="dark"
      enableSystem
      disableTransitionOnChange
      storageKey="sf-theme"
      nonce={nonce}
    >
      {children}
    </NextThemes>
  );
}
