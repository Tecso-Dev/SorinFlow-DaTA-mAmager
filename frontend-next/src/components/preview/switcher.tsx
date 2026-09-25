"use client";

import { useEffect } from "react";
import { useTheme } from "next-themes";
import { cn } from "cn";
import type { Variant } from "./shell";

const NAMES: Record<Variant, string> = { a: "الف · شب نیلی", b: "ب · گرافیت", c: "ج · فیروزه" };

/** Floating picker for comparing the three proposals. Also copies the
 *  variant onto <body>, so menus and sheets (portaled there) match it. */
export function PreviewSwitcher({ current, theme }: { current: Variant; theme?: "light" | "dark" }) {
  const { setTheme } = useTheme();

  useEffect(() => {
    document.body.dataset.variant = current;
    return () => {
      delete document.body.dataset.variant;
    };
  }, [current]);

  useEffect(() => {
    if (theme) setTheme(theme);
  }, [theme, setTheme]);

  return (
    <div
      data-switcher
      className={cn(
        "fixed bottom-4 end-4 z-40 flex items-center gap-1 rounded-full border bg-popover/95 p-1 text-xs shadow-xl backdrop-blur",
        current === "c" && "max-lg:bottom-24",
      )}
    >
      {(Object.keys(NAMES) as Variant[]).map((v) => (
        <a
          key={v}
          href={`/preview/${v}`}
          aria-current={v === current ? "page" : undefined}
          className={cn(
            "rounded-full px-3 py-1.5 text-muted-foreground hover:text-foreground",
            v === current && "bg-primary font-semibold text-primary-foreground hover:text-primary-foreground",
          )}
        >
          {NAMES[v]}
        </a>
      ))}
    </div>
  );
}
