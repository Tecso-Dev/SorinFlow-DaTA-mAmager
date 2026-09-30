// CRM vocabulary shared by every CRM tab: the stored values and how the
// panel names them. The same labels as the old panel (frontend/js/app.js),
// so the two panels never call one status two things while both run.

import { faNum } from "./format";

/** A tone the badges map to theme tokens (never a raw colour). */
export type Tone = "neutral" | "info" | "warning" | "success" | "danger" | "primary" | "violet";

export type Labeled = { label: string; tone: Tone };

export const LEAD_STATUS: Record<string, Labeled> = {
  new: { label: "جدید", tone: "warning" },
  contacted: { label: "تماس گرفته", tone: "info" },
  visit: { label: "بازدید از فایل", tone: "warning" },
  contract_meeting: { label: "نشست و تنظیم قرارداد", tone: "warning" },
  qualified: { label: "واجد شرایط", tone: "primary" },
  closed: { label: "بسته شده", tone: "success" },
  rented: { label: "اجاره شده", tone: "violet" },
  rejected: { label: "رد شده", tone: "danger" },
};
/** In the order a lead moves (crm.VALID_LEAD_STATUSES). */
export const LEAD_STATUS_ORDER = ["new", "contacted", "qualified", "visit", "contract_meeting", "closed", "rented", "rejected"];

/** POST /crm/leads/{id}/call outcomes (app/crm/call_queue.OUTCOMES). */
export const CALL_OUTCOME: Record<string, string> = {
  answered: "پاسخ داد",
  no_answer: "پاسخ نداد",
  busy: "مشغول بود",
  callback: "خواست دوباره تماس بگیریم",
  visit: "بازدید گذاشتیم",
  not_interested: "علاقه‌ای ندارد",
  wrong_number: "شماره اشتباه است",
};

export const TASK_PRIORITY: Record<string, Labeled> = {
  low: { label: "کم", tone: "neutral" },
  medium: { label: "متوسط", tone: "info" },
  high: { label: "زیاد", tone: "warning" },
  urgent: { label: "فوری", tone: "danger" },
};
export const TASK_STATUS: Record<string, Labeled> = {
  todo: { label: "انجام نشده", tone: "neutral" },
  in_progress: { label: "در حال انجام", tone: "primary" },
  done: { label: "انجام شده", tone: "success" },
};

export const DEAL_STATUS: Record<string, Labeled> = {
  new: { label: "جدید", tone: "warning" },
  negotiating: { label: "مذاکره", tone: "info" },
  contract: { label: "قرارداد", tone: "primary" },
  closed: { label: "بسته", tone: "success" },
  cancelled: { label: "لغو", tone: "danger" },
};
export const DEAL_TYPE: Record<string, string> = { buy: "خرید", rent: "اجاره", lease: "رهن" };

export const CONTACT_TYPE: Record<string, Labeled> = {
  owner: { label: "مالکین", tone: "primary" },
  landlord: { label: "موجرین", tone: "info" },
  tenant: { label: "مستاجرین", tone: "success" },
  seeker: { label: "خواهان", tone: "warning" },
  builder: { label: "سازندگان", tone: "violet" },
  agency: { label: "املاک", tone: "danger" },
  // older values, still on some rows
  buyer: { label: "خواهان", tone: "warning" },
  consultant: { label: "املاک", tone: "danger" },
  other: { label: "سایر", tone: "neutral" },
};
/** The six a person can choose. */
export const CONTACT_TYPES = ["owner", "landlord", "tenant", "seeker", "builder", "agency"];
export const CONTACT_CATEGORY: Record<string, Labeled> = {
  VIP: { label: "VIP", tone: "warning" },
  normal: { label: "عادی", tone: "info" },
  cold: { label: "سرد", tone: "neutral" },
};

export const TEMPERATURE: Record<string, Labeled> = {
  hot: { label: "داغ", tone: "danger" },
  warm: { label: "گرم", tone: "warning" },
  cold: { label: "سرد", tone: "info" },
};
/** crm.VALID_CUSTOMER_SOURCES; «portal» is written by the portal itself. */
export const CUSTOMER_SOURCE: Record<string, string> = { in_person: "حضوری", divar: "دیوار", referral: "معرف", portal: "پرتال" };
export const SHOWING_STEP: Record<string, string> = { meeting: "نشست", archive: "بایگانی", second_visit: "بازدید دوم" };

export const PROPERTY_KIND: Record<string, string> = {
  apartment: "آپارتمان", villa: "ویلایی", old_house: "کلنگی", land: "زمین", shop: "مغازه", office: "دفتر کار",
};
export const ADVERTISER: Record<string, string> = { personal: "شخصی", agency: "املاک" };

export const DIRECTION_OPTIONS = [
  "شمالی", "جنوبی", "شرقی", "غربی", "شمالی جنوبی", "شرقی غربی", "شمالی شرقی", "شمالی غربی", "جنوبی شرقی", "جنوبی غربی",
];
export const CORNER_OPTIONS = ["دونبش", "سه‌نبش"];

export const REMINDER_REPEAT: Record<string, string> = { none: "بدون تکرار", daily: "روزانه", weekly: "هفتگی", monthly: "ماهانه" };
export const REMINDER_CHANNEL: Record<string, string> = { in_app: "در برنامه", sms: "پیامک" };

/**
 * Money the way the old panel wrote it: «۳٫۵ میلیارد», «۸۵۰ میلیون», one
 * decimal and never rounded up to the next whole (3.5 billion is not 4).
 * «—» for nothing.
 */
export function price(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n) || n === 0) return "—";
  const one = (v: number) => faNum(Math.round(v * 10) / 10, { maximumFractionDigits: 1 });
  if (n >= 1e9) return `${one(n / 1e9)} میلیارد`;
  if (n >= 1e6) return `${one(n / 1e6)} میلیون`;
  return `${faNum(n)} تومان`;
}

/**
 * A rent as one comparable figure: deposit plus 30 months of rent, the
 * office's «رهن کامل» rule (the old panel's _matchMoney).
 */
export function fullDeposit(deposit: number | null | undefined, rent: number | null | undefined): number | null {
  if (!deposit && !rent) return null;
  return (deposit ?? 0) + 30 * (rent ?? 0);
}

/** «/api/crm/leads/export/excel?…» for a download link: GET, the session cookie rides along. */
export function exportHref(path: string, params?: Record<string, string | number | boolean | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
  }
  const s = q.toString();
  return `/api${path}${s ? `?${s}` : ""}`;
}

/** A query string from filters, leaving out the empty ones. */
export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== "" && v !== false) q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}
