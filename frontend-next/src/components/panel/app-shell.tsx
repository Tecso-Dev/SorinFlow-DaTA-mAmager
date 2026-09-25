"use client";

import { Bell, Command as CommandIcon, LogOut, Menu, Moon, Search, Sun, UserRound } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { cn } from "cn";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList,
} from "@/components/ui/command";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { api, onApiError } from "@/lib/api";
import { can, displayName, ROLE_LABEL, SESSION_KEY, useSession, type User } from "@/lib/session";
import type { SiteConfig } from "@/lib/site";
import { toast } from "@/components/toaster";
import { NAV, navItemFor } from "./nav";

export type ShellSite = Pick<SiteConfig, "brandName" | "brandNameLatin" | "tagline">;

function BrandMark({ site, compact = false }: { site: ShellSite; compact?: boolean }) {
  return (
    <Link href="/panel" className="flex items-center gap-2.5 rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-ring">
      <div
        aria-hidden
        className="grid size-9 place-items-center rounded-xl bg-linear-to-br from-indigo-500 to-violet-600 text-sm font-black text-white shadow-[0_0_24px_-4px_rgb(99_102_241/0.7)]"
      >
        {site.brandNameLatin.slice(0, 1)}
      </div>
      {!compact && (
        <div className="leading-tight">
          <div className="text-[15px] font-extrabold tracking-tight">{site.brandName}</div>
          <div className="text-[11px] text-muted-foreground">{site.tagline}</div>
        </div>
      )}
    </Link>
  );
}

function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label="تغییر تم روشن و تیره"
      onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
    >
      <Sun className="size-[18px] dark:hidden" />
      <Moon className="hidden size-[18px] dark:block" />
    </Button>
  );
}

function initialOf(u: User) {
  return displayName(u).slice(0, 1);
}

function useLogout() {
  const router = useRouter();
  const qc = useQueryClient();
  return async () => {
    try {
      await api("/session/logout", { method: "POST" });
    } finally {
      qc.clear();
      router.replace("/panel/login");
    }
  };
}

function UserMenu({ user, withName = false }: { user: User; withName?: boolean }) {
  const logout = useLogout();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          className={cn(
            "flex items-center gap-2.5 rounded-xl p-1 text-start outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring",
            withName && "w-full p-2",
          )}
          aria-label="منوی حساب"
        >
          <Avatar className="size-8">
            {user.avatar_url && <AvatarImage src={user.avatar_url} alt="" />}
            <AvatarFallback className="bg-primary/15 text-sm font-bold text-primary">{initialOf(user)}</AvatarFallback>
          </Avatar>
          {withName && (
            <span className="min-w-0 flex-1 leading-tight">
              <span className="block truncate text-sm font-semibold">{displayName(user)}</span>
              <span className="block truncate text-xs text-muted-foreground">{user.headline || ROLE_LABEL[user.role]}</span>
            </span>
          )}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel className="truncate">{displayName(user)}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link href="/panel/profile">
            <UserRound /> پروفایل و امنیت
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem variant="destructive" onSelect={() => void logout()}>
          <LogOut /> خروج
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function NavList({ user, onNavigate }: { user: User; onNavigate?: () => void }) {
  const pathname = usePathname();
  const active = navItemFor(pathname)?.key;
  return (
    <nav className="flex flex-col gap-5" aria-label="بخش‌های پنل">
      {NAV.map((g) => {
        const items = g.items.filter((i) => can(user, i));
        if (!items.length) return null;
        return (
          <div key={g.label} className="flex flex-col gap-0.5">
            <div className="mb-1 px-3 text-[11px] font-semibold text-muted-foreground/80">{g.label}</div>
            {items.map((it) => {
              const on = it.key === active;
              return (
                <Link
                  key={it.key}
                  href={it.href}
                  onClick={onNavigate}
                  aria-current={on ? "page" : undefined}
                  className={cn(
                    "group relative flex items-center gap-3 rounded-xl px-3 py-2 text-sm text-sidebar-foreground outline-none transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-ring",
                    on &&
                      "bg-linear-to-l from-indigo-500/20 to-violet-500/5 font-semibold text-sidebar-accent-foreground ring-1 ring-inset ring-indigo-400/20",
                  )}
                >
                  {on && <span className="absolute inset-y-2 start-0 w-[3px] rounded-full bg-primary shadow-[0_0_12px_rgb(99_102_241)]" />}
                  <it.icon className={cn("size-[18px] shrink-0 opacity-70", on && "text-primary opacity-100")} />
                  <span className="flex-1 truncate">{it.label}</span>
                </Link>
              );
            })}
          </div>
        );
      })}
    </nav>
  );
}

function CommandPalette({ user, open, onOpenChange }: { user: User; open: boolean; onOpenChange: (v: boolean) => void }) {
  const router = useRouter();
  const { resolvedTheme, setTheme } = useTheme();
  const logout = useLogout();
  const go = (href: string) => {
    onOpenChange(false);
    router.push(href);
  };
  return (
    <CommandDialog open={open} onOpenChange={onOpenChange} title="جستجو یا رفتن به…" description="نام یک بخش یا کار را بنویسید">
      <CommandInput placeholder="جستجو یا رفتن به…" />
      <CommandList>
        <CommandEmpty>چیزی پیدا نشد.</CommandEmpty>
        {NAV.map((g) => {
          const items = g.items.filter((i) => can(user, i));
          if (!items.length) return null;
          return (
            <CommandGroup key={g.label} heading={g.label}>
              {items.map((it) => (
                <CommandItem key={it.key} value={`${it.label} ${it.key}`} onSelect={() => go(it.href)}>
                  <it.icon /> {it.label}
                </CommandItem>
              ))}
            </CommandGroup>
          );
        })}
        <CommandGroup heading="کارها">
          <CommandItem value="پروفایل امنیت رمز profile" onSelect={() => go("/panel/profile")}>
            <UserRound /> پروفایل و امنیت
          </CommandItem>
          <CommandItem
            value="تم روشن تیره theme"
            onSelect={() => {
              setTheme(resolvedTheme === "dark" ? "light" : "dark");
              onOpenChange(false);
            }}
          >
            <Moon /> تغییر تم
          </CommandItem>
          <CommandItem value="خروج logout" onSelect={() => void logout()}>
            <LogOut /> خروج
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}

function SearchButton({ onClick, className }: { onClick: () => void; className?: string }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex h-9 items-center gap-2 rounded-xl border bg-background/60 px-3 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
        className,
      )}
    >
      <Search className="size-4" />
      <span className="flex-1 text-start">جستجو یا رفتن به…</span>
      <kbd className="hidden items-center gap-0.5 rounded border bg-muted px-1.5 font-sans text-[10px] sm:flex" dir="ltr">
        <CommandIcon className="size-3" />K
      </kbd>
    </button>
  );
}

function ShellSkeleton() {
  return (
    <div className="flex min-h-dvh">
      <aside className="hidden w-[272px] shrink-0 border-e bg-sidebar p-5 lg:block">
        <Skeleton className="h-9 w-40" />
        <div className="mt-8 flex flex-col gap-3">
          {Array.from({ length: 9 }, (_, i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </div>
      </aside>
      <main className="flex-1 p-8">
        <Skeleton className="h-8 w-64" />
        <div className="mt-6 grid gap-4 sm:grid-cols-3">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-32" />
          ))}
        </div>
      </main>
    </div>
  );
}

export function AppShell({ site, children }: { site: ShellSite; children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const qc = useQueryClient();
  const session = useSession();
  const [palette, setPalette] = useState(false);
  const [sheet, setSheet] = useState(false);

  // The session ends (expired, password changed elsewhere, logged out in
  // another tab): back to the login page, then straight back here after it.
  useEffect(
    () =>
      onApiError((e) => {
        if (e.status === 401) {
          qc.removeQueries({ queryKey: SESSION_KEY });
          router.replace(`/panel/login?next=${encodeURIComponent(pathname)}`);
        } else if (e.status === 503) {
          toast.error("سامانه در حال به‌روزرسانی است", "چند دقیقهٔ دیگر دوباره امتحان کنید.");
        }
      }),
    [qc, router, pathname],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (session.isPending || session.isError) return <ShellSkeleton />;
  const user = session.data.user;

  return (
    <div className="flex min-h-dvh">
      <div className="hidden shrink-0 border-e bg-sidebar lg:block">
        <aside className="sticky top-0 flex h-dvh w-[272px] flex-col">
          <div className="flex h-16 items-center px-5">
            <BrandMark site={site} />
          </div>
          <div className="px-4 pb-3">
            <SearchButton className="w-full" onClick={() => setPalette(true)} />
          </div>
          <div className="flex-1 overflow-y-auto px-3 py-2">
            <NavList user={user} />
          </div>
          <div className="border-t p-3">
            <UserMenu user={user} withName />
          </div>
        </aside>
      </div>

      <div className="relative flex min-w-0 flex-1 flex-col">
        <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
          <div className="absolute -top-40 start-[10%] size-[520px] rounded-full bg-(--glow-1) blur-3xl" />
          <div className="absolute top-[40%] -end-40 size-[420px] rounded-full bg-(--glow-2) blur-3xl" />
        </div>
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b bg-background/75 px-4 backdrop-blur-xl sm:px-6 lg:px-8">
          <Sheet open={sheet} onOpenChange={setSheet}>
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
                <NavList user={user} onNavigate={() => setSheet(false)} />
              </div>
            </SheetContent>
          </Sheet>
          <div className="lg:hidden">
            <BrandMark site={site} compact />
          </div>
          <div className="ms-auto flex items-center gap-1.5">
            <SearchButton className="hidden w-64 md:flex lg:hidden" onClick={() => setPalette(true)} />
            <Button variant="ghost" size="icon" className="md:hidden" aria-label="جستجو" onClick={() => setPalette(true)}>
              <Search className="size-[18px]" />
            </Button>
            <Button variant="ghost" size="icon" aria-label="اعلان‌ها">
              <Bell className="size-[18px]" />
            </Button>
            <ThemeToggle />
            <div className="lg:hidden">
              <UserMenu user={user} />
            </div>
          </div>
        </header>
        <main id="main" className="relative flex-1 px-4 py-6 sm:px-6 lg:px-8">
          {children}
        </main>
      </div>
      <CommandPalette user={user} open={palette} onOpenChange={setPalette} />
    </div>
  );
}
