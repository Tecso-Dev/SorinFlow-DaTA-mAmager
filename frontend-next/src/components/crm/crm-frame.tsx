"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";
import { cn } from "cn";
import { Empty } from "@/components/panel/kit";
import { can, useSession } from "@/lib/session";
import { CRM_TABS } from "./tabs";

/** The CRM section: its tab strip (a row of links, one route per tab, so a
 *  tab can be bookmarked and the back button works) and the tab itself. */
export function CrmFrame({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const user = useSession().data?.user;
  const strip = useRef<HTMLElement>(null);
  const tabs = CRM_TABS.filter((t) => can(user, { perm: "crm" }) && (!t.perm || can(user, { perm: t.perm })));

  // keep the current tab in view on a phone, where the strip scrolls
  useEffect(() => {
    strip.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [pathname]);

  if (user && !can(user, { perm: "crm" })) {
    return <Empty>دسترسی «CRM» برای حساب شما فعال نیست.</Empty>;
  }
  return (
    <div className="mx-auto flex max-w-[1480px] flex-col gap-5">
      <nav
        ref={strip}
        aria-label="بخش‌های CRM"
        className="-mx-4 flex gap-1 overflow-x-auto px-4 pb-1 [scrollbar-width:none] sm:mx-0 sm:flex-wrap sm:px-0"
      >
        {tabs.map((t) => {
          const href = `/panel/crm/${t.slug}`;
          const active = pathname === href || pathname.startsWith(href + "/");
          return (
            <Link
              key={t.slug}
              href={href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex shrink-0 items-center gap-1.5 rounded-xl px-3 py-2 text-sm font-medium text-muted-foreground transition-colors",
                "outline-none hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring",
                active && "bg-primary/12 font-semibold text-foreground hover:bg-primary/15 hover:text-foreground [&_svg]:text-primary",
              )}
            >
              <t.icon className="size-4" />
              {t.label}
            </Link>
          );
        })}
      </nav>
      {children}
    </div>
  );
}
