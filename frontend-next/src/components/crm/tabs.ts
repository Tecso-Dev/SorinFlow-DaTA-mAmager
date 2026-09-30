import {
  Bell, BookUser, CalendarDays, ChartPie, ClipboardCheck, FolderArchive, Handshake, ListChecks, MessageSquareText,
  NotebookPen, PhoneCall, Target, Users, type LucideIcon,
} from "lucide-react";

export type CrmTab = {
  slug: string;
  label: string;
  icon: LucideIcon;
  /** a permission beyond «crm» (the filing router has its own) */
  perm?: string;
};

// The old panel's 13 CRM tabs, in its order of daily use.
export const CRM_TABS: CrmTab[] = [
  { slug: "calls", label: "تماس‌های امروز", icon: PhoneCall },
  { slug: "leads", label: "لیدها", icon: Target },
  { slug: "customers", label: "مشتریان", icon: Users },
  { slug: "contacts", label: "دفترچه تلفن", icon: BookUser },
  { slug: "deals", label: "معاملات", icon: Handshake },
  { slug: "tasks", label: "وظایف", icon: ListChecks },
  { slug: "calendar", label: "تقویم", icon: CalendarDays },
  { slug: "filing", label: "کمد و زونکن", icon: FolderArchive, perm: "filing" },
  { slug: "reminders", label: "یادآورها", icon: Bell },
  { slug: "notes", label: "یادداشت‌ها", icon: NotebookPen },
  { slug: "sms", label: "پیامک", icon: MessageSquareText },
  { slug: "dpa", label: "ارزیابی روزانه", icon: ClipboardCheck },
  { slug: "report", label: "گزارش", icon: ChartPie },
];

export const crmTab = (slug: string) => CRM_TABS.find((t) => t.slug === slug);
