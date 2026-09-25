import {
  Activity, Bot, Building2, Inbox, KeyRound, LayoutDashboard, Mail, MessageSquareText, Palette, ScanEye,
  ScrollText, ShieldCheck, Smartphone, Sparkles, UserCog, Users, type LucideIcon,
} from "lucide-react";
import type { Role } from "@/lib/session";

export type NavItem = {
  key: string;
  label: string;
  href: string;
  icon: LucideIcon;
  /** permission key the backend checks on the same router */
  perm?: string;
  roles?: Role[];
  /** the Phase 4 step that rebuilds it; until then the page says so */
  step: number;
  /** its place in the old panel, for the "use the current panel" link */
  legacy: string;
};

const BOSS: Role[] = ["root", "super_admin"];

// Same keys as the old panel's NAV_PERMISSION / NAV_ROLE_ONLY (app.js).
export const NAV: { label: string; items: NavItem[] }[] = [
  {
    label: "کار روزانه",
    items: [
      { key: "dashboard", label: "داشبورد", href: "/panel", icon: LayoutDashboard, perm: "stats", step: 2, legacy: "dashboard" },
      { key: "properties", label: "لیست املاک", href: "/panel/properties", icon: Building2, perm: "properties", step: 4, legacy: "properties" },
      { key: "crm", label: "CRM و لیدها", href: "/panel/crm", icon: Users, perm: "crm", step: 3, legacy: "crm" },
      { key: "portal", label: "درخواست‌های مشتریان", href: "/panel/portal", icon: Inbox, perm: "portal", step: 8, legacy: "portal" },
    ],
  },
  {
    label: "اسکرپ و داده",
    items: [
      { key: "scraper", label: "اسکرپر", href: "/panel/scraper", icon: Bot, perm: "scraper", step: 6, legacy: "scraper" },
      { key: "divar", label: "احراز هویت دیوار", href: "/panel/divar", icon: KeyRound, perm: "divar_auth", step: 6, legacy: "auth" },
      { key: "proxies", label: "پراکسی‌ها", href: "/panel/proxies", icon: ShieldCheck, perm: "proxies", step: 6, legacy: "proxies" },
      { key: "insights", label: "هوش تصویری", href: "/panel/insights", icon: ScanEye, perm: "crm", step: 4, legacy: "insights" },
    ],
  },
  {
    label: "ارتباطات",
    items: [
      { key: "sms", label: "پیامک", href: "/panel/sms", icon: MessageSquareText, perm: "sms", step: 7, legacy: "sms" },
      { key: "email", label: "ایمیل", href: "/panel/email", icon: Mail, perm: "email", step: 7, legacy: "email" },
      { key: "forwarder", label: "فرستندهٔ پیامک", href: "/panel/forwarder", icon: Smartphone, perm: "forwarder", step: 6, legacy: "forwarder" },
    ],
  },
  {
    label: "سیستم",
    items: [
      { key: "ai", label: "هوش مصنوعی", href: "/panel/ai", icon: Sparkles, roles: BOSS, step: 7, legacy: "ai" },
      { key: "monitoring", label: "پایش سامانه", href: "/panel/monitoring", icon: Activity, perm: "monitoring", step: 7, legacy: "monitoring" },
      { key: "users", label: "کاربران و بکاپ", href: "/panel/users", icon: UserCog, roles: BOSS, step: 7, legacy: "users" },
      { key: "audit", label: "رویدادها", href: "/panel/audit", icon: ScrollText, roles: BOSS, step: 7, legacy: "audit" },
      { key: "settings", label: "برند و سایت", href: "/panel/settings", icon: Palette, roles: ["root"], step: 7, legacy: "users" },
    ],
  },
];

export const NAV_ITEMS = NAV.flatMap((g) => g.items);

/** The longest href that prefixes the path wins (/panel is everyone's prefix). */
export function navItemFor(pathname: string): NavItem | undefined {
  return NAV_ITEMS.filter((i) => pathname === i.href || pathname.startsWith(i.href + "/")).sort(
    (a, b) => b.href.length - a.href.length,
  )[0];
}
