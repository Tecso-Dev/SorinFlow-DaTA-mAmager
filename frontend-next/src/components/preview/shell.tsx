"use client";

import {
  Activity, Bell, Bot, Building2, Command, Inbox, KeyRound, LayoutDashboard, LogOut, Mail, Menu,
  MessageSquareText, Moon, ScanEye, ScrollText, Search, Settings, ShieldCheck, Smartphone, Sparkles,
  Sun, UserCog, Users,
} from "lucide-react";
import { useTheme } from "next-themes";
import { cn } from "cn";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { faNum } from "@/lib/format";
import type { SiteConfig } from "@/lib/site";
import { me, navGroups, type NavKey } from "./data";

export type Variant = "a" | "b" | "c";

const ICONS: Record<NavKey, React.ComponentType<{ className?: string }>> = {
  dashboard: LayoutDashboard, properties: Building2, crm: Users, portal: Inbox, scraper: Bot,
  divar: KeyRound, proxies: ShieldCheck, insights: ScanEye, sms: MessageSquareText, email: Mail,
  forwarder: Smartphone, ai: Sparkles, monitoring: Activity, users: UserCog, audit: ScrollText,
};

function BrandMark({ site, compact = false }: { site: SiteConfig; compact?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <div
        className={cn(
          "grid size-9 place-items-center rounded-xl text-sm font-black text-white",
          "va:bg-linear-to-br va:from-indigo-500 va:to-violet-600 va:shadow-[0_0_24px_-4px_rgb(99_102_241/0.7)]",
          "vb:size-7 vb:rounded-md vb:bg-foreground vb:text-background",
          "vc:rounded-2xl vc:bg-linear-to-br vc:from-teal-500 vc:to-cyan-700",
        )}
        aria-hidden
      >
        {site.brandNameLatin.slice(0, 1)}
      </div>
      {!compact && (
        <div className="leading-tight">
          <div className="text-[15px] font-extrabold tracking-tight vb:text-sm vb:font-bold">{site.brandName}</div>
          <div className="text-[11px] text-muted-foreground vb:hidden">{site.tagline}</div>
        </div>
      )}
    </div>
  );
}

function ThemeToggle({ className }: { className?: string }) {
  const { resolvedTheme, setTheme } = useTheme();
  return (
    <Button
      variant="ghost"
      size="icon"
      className={className}
      aria-label="تغییر تم روشن و تیره"
      onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
    >
      <Sun className="size-[18px] dark:hidden" />
      <Moon className="hidden size-[18px] dark:block" />
    </Button>
  );
}

function UserMenu({ withName = false }: { withName?: boolean }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          className={cn(
            "flex items-center gap-2.5 rounded-xl p-1 text-start outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring",
            withName && "w-full p-2",
          )}
        >
          <Avatar className="size-8 vc:size-9">
            <AvatarFallback className="bg-primary/15 text-sm font-bold text-primary">{me.initials}</AvatarFallback>
          </Avatar>
          {withName && (
            <span className="min-w-0 flex-1 leading-tight">
              <span className="block truncate text-sm font-semibold">{me.name}</span>
              <span className="block text-xs text-muted-foreground">{me.role}</span>
            </span>
          )}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-52">
        <DropdownMenuLabel>{me.name}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem><Settings /> پروفایل و امنیت</DropdownMenuItem>
        <DropdownMenuItem variant="destructive"><LogOut /> خروج</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function SearchButton({ className }: { className?: string }) {
  return (
    <button
      className={cn(
        "flex h-9 items-center gap-2 rounded-xl border bg-background/60 px-3 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
        "vb:h-8 vb:rounded-md vc:h-10 vc:rounded-full vc:bg-muted/60 vc:border-transparent",
        className,
      )}
    >
      <Search className="size-4" />
      <span className="flex-1 text-start">جستجو یا رفتن به…</span>
      <kbd className="hidden items-center gap-0.5 rounded border bg-muted px-1.5 font-sans text-[10px] sm:flex" dir="ltr">
        <Command className="size-3" />K
      </kbd>
    </button>
  );
}

function NavList({ active = "dashboard" }: { active?: NavKey }) {
  return (
    <nav className="flex flex-col gap-5 vb:gap-4">
      {navGroups.map((g) => (
        <div key={g.label} className="flex flex-col gap-0.5">
          <div className="mb-1 px-3 text-[11px] font-semibold text-muted-foreground/80 vb:px-2 vb:text-[11px] vb:font-medium">
            {g.label}
          </div>
          {g.items.map((it) => {
            const Icon = ICONS[it.key];
            const on = it.key === active;
            return (
              <a
                key={it.key}
                href="#"
                aria-current={on ? "page" : undefined}
                className={cn(
                  "group relative flex items-center gap-3 rounded-xl px-3 py-2 text-sm text-sidebar-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
                  "vb:gap-2.5 vb:rounded-md vb:px-2 vb:py-1.5 vb:text-[13px]",
                  on && "bg-sidebar-accent font-semibold text-sidebar-accent-foreground",
                  on && "va:bg-linear-to-l va:from-indigo-500/20 va:to-violet-500/5 va:ring-1 va:ring-inset va:ring-indigo-400/20",
                )}
              >
                {on && (
                  <span className="absolute inset-y-2 start-0 w-[3px] rounded-full bg-primary va:shadow-[0_0_12px_rgb(99_102_241)] vb:inset-y-1.5" />
                )}
                <Icon className={cn("size-[18px] shrink-0 opacity-70 vb:size-4", on && "text-primary opacity-100")} />
                <span className="flex-1 truncate">{it.label}</span>
                {"badge" in it && (
                  <Badge variant="secondary" className="h-5 min-w-5 justify-center rounded-full px-1.5 text-[11px] tabular">
                    {faNum(it.badge)}
                  </Badge>
                )}
              </a>
            );
          })}
        </div>
      ))}
    </nav>
  );
}

function SidebarShell({ site, children }: { site: SiteConfig; children: React.ReactNode }) {
  return (
    <div className="flex min-h-dvh">
      <aside className="sticky top-0 hidden h-dvh w-[272px] shrink-0 flex-col border-e bg-sidebar lg:flex vb:w-60">
        <div className="flex h-16 items-center px-5 vb:h-12 vb:px-4">
          <BrandMark site={site} />
        </div>
        <div className="px-4 pb-3 vb:px-3">
          <SearchButton className="w-full" />
        </div>
        <div className="flex-1 overflow-y-auto px-3 py-2 vb:px-2">
          <NavList />
        </div>
        <div className="border-t p-3 vb:p-2">
          <UserMenu withName />
        </div>
      </aside>

      <div className="relative flex min-w-0 flex-1 flex-col">
        {/* A's soft glow; transparent for B */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 overflow-hidden"
        >
          <div className="absolute -top-40 start-[10%] size-[520px] rounded-full bg-(--glow-1) blur-3xl" />
          <div className="absolute top-[40%] -end-40 size-[420px] rounded-full bg-(--glow-2) blur-3xl" />
        </div>
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b bg-background/75 px-4 backdrop-blur-xl sm:px-6 lg:px-8 vb:h-12">
          <Sheet>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" className="lg:hidden" aria-label="باز کردن منو">
                <Menu />
              </Button>
            </SheetTrigger>
            <SheetContent side="right" className="w-[288px] gap-0 bg-sidebar p-0">
              <SheetTitle className="flex h-16 items-center px-5">
                <BrandMark site={site} />
              </SheetTitle>
              <div className="flex-1 overflow-y-auto px-3 pb-6">
                <NavList />
              </div>
            </SheetContent>
          </Sheet>
          <div className="hidden text-sm text-muted-foreground vb:flex vb:items-center vb:gap-1.5 max-lg:vb:hidden">
            <span>کار روزانه</span>
            <span className="opacity-50">/</span>
            <span className="font-medium text-foreground">داشبورد</span>
          </div>
          <div className="lg:hidden">
            <BrandMark site={site} compact />
          </div>
          <div className="ms-auto flex items-center gap-1.5">
            <SearchButton className="hidden w-64 md:flex lg:hidden" />
            <Button variant="ghost" size="icon" className="relative" aria-label="اعلان‌ها">
              <Bell className="size-[18px]" />
              <span className="absolute top-2 end-2 size-2 rounded-full bg-destructive ring-2 ring-background" />
            </Button>
            <ThemeToggle />
            <div className="lg:hidden">
              <UserMenu />
            </div>
          </div>
        </header>
        <main className="relative flex-1 px-4 py-6 sm:px-6 lg:px-8 vb:py-5">{children}</main>
      </div>
    </div>
  );
}

const TOP_NAV: { key: NavKey; label: string }[] = [
  { key: "dashboard", label: "داشبورد" },
  { key: "properties", label: "املاک" },
  { key: "crm", label: "مشتریان و لیدها" },
  { key: "scraper", label: "اسکرپر" },
  { key: "sms", label: "ارتباطات" },
  { key: "monitoring", label: "سیستم" },
];

function TopNavShell({ site, children }: { site: SiteConfig; children: React.ReactNode }) {
  return (
    <div className="relative min-h-dvh">
      <header className="sticky top-0 z-30 border-b bg-background/80 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-[1320px] items-center gap-4 px-4 sm:px-6">
          <BrandMark site={site} />
          <nav className="ms-4 hidden items-center gap-1 rounded-full bg-muted/70 p-1 lg:flex">
            {TOP_NAV.map((it) => (
              <a
                key={it.key}
                href="#"
                aria-current={it.key === "dashboard" ? "page" : undefined}
                className={cn(
                  "rounded-full px-4 py-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground",
                  it.key === "dashboard" && "bg-card font-semibold text-foreground shadow-sm",
                )}
              >
                {it.label}
              </a>
            ))}
          </nav>
          <div className="ms-auto flex items-center gap-1.5">
            <SearchButton className="hidden w-56 xl:flex" />
            <Button variant="ghost" size="icon" className="relative rounded-full" aria-label="اعلان‌ها">
              <Bell className="size-[18px]" />
              <span className="absolute top-2 end-2 size-2 rounded-full bg-destructive ring-2 ring-background" />
            </Button>
            <ThemeToggle className="rounded-full" />
            <UserMenu />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1320px] px-4 py-6 pb-28 sm:px-6 lg:pb-12">{children}</main>
      <nav className="fixed inset-x-3 bottom-3 z-30 grid grid-cols-5 rounded-2xl border bg-card/90 p-1.5 shadow-lg backdrop-blur-xl lg:hidden">
        {(["dashboard", "properties", "crm", "scraper", "monitoring"] as NavKey[]).map((k) => {
          const Icon = ICONS[k];
          const on = k === "dashboard";
          const label = TOP_NAV.find((t) => t.key === k)?.label ?? "";
          return (
            <a
              key={k}
              href="#"
              aria-current={on ? "page" : undefined}
              className={cn(
                "flex flex-col items-center gap-1 rounded-xl py-1.5 text-[11px] text-muted-foreground",
                on && "bg-primary/10 font-semibold text-primary",
              )}
            >
              <Icon className="size-5" />
              <span className="max-w-full truncate px-1">{label}</span>
            </a>
          );
        })}
      </nav>
    </div>
  );
}

export function PreviewShell({ variant, site, children }: { variant: Variant; site: SiteConfig; children: React.ReactNode }) {
  return variant === "c" ? <TopNavShell site={site}>{children}</TopNavShell> : <SidebarShell site={site}>{children}</SidebarShell>;
}
