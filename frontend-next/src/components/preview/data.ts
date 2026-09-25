// Invented sample data for the design proposals only (no real customer or
// number; the signed-in user is the viewer). Milestone 2 replaces it with
// /api/stats/*, /api/crm/* calls.

import { faDate } from "@/lib/format";

// Deterministic pseudo-random so server and browser render the same numbers.
function rng(seed: number) {
  let s = seed;
  return () => {
    s = (s * 1664525 + 1013904223) % 4294967296;
    return s / 4294967296;
  };
}

const r = rng(1405);
const today = new Date();
today.setHours(12, 0, 0, 0);

export const trend = Array.from({ length: 30 }, (_, i) => {
  const d = new Date(today);
  d.setDate(d.getDate() - (29 - i));
  const weekend = d.getDay() === 5; // Friday is quiet on Divar too
  const base = 82 + i * 1.3 + Math.sin(i / 4) * 9;
  const listings = Math.round((weekend ? 0.78 : 1) * (base + r() * 14));
  const leads = Math.round(listings * (0.36 + r() * 0.06));
  const deals = Math.round(leads * (0.05 + r() * 0.05));
  return { day: faDate(d, { day: "numeric", month: "short" }), listings, leads, deals };
});

export const spark = (seed: number, n = 14, drift = 1) => {
  const g = rng(seed);
  let v = 50;
  return Array.from({ length: n }, (_, i) => {
    v = Math.max(8, v + (g() - 0.42) * 14 * drift);
    return { i, v: Math.round(v) };
  });
};

export type KpiKey = "listings" | "leads" | "hot" | "calls" | "deals" | "conversion";

export const kpis: {
  key: KpiKey;
  label: string;
  value: number;
  unit?: string;
  delta: number;
  hint: string;
  of?: number;
  seed: number;
}[] = [
  { key: "listings", label: "آگهی‌های تازهٔ امروز", value: 128, delta: 18, hint: "نسبت به دیروز", seed: 3 },
  { key: "leads", label: "لیدهای فعال", value: 462, delta: 6.4, hint: "در ۷ روز گذشته", seed: 7 },
  { key: "hot", label: "مشتریان داغ", value: 37, delta: 3, hint: "۵ نفر امروز تماس می‌خواهند", seed: 11 },
  { key: "calls", label: "تماس‌های امروز", value: 24, of: 31, delta: -4, hint: "۷ تماس مانده", seed: 13 },
  { key: "deals", label: "قرارداد این ماه", value: 9, delta: 12.5, hint: "کمیسیون: ۴۸۰ میلیون", seed: 17 },
  { key: "conversion", label: "نرخ تبدیل لید", value: 6.8, unit: "٪", delta: 0.9, hint: "لید به قرارداد، ۳۰ روز", seed: 19 },
];

export const funnel = [
  { stage: "لید تازه", value: 462 },
  { stage: "تماس گرفته‌شده", value: 287 },
  { stage: "بازدید", value: 94 },
  { stage: "مذاکره", value: 31 },
  { stage: "قرارداد", value: 9 },
];

export type CallItem = {
  name: string;
  topic: string;
  time: string;
  status: "due" | "done" | "missed" | "later";
  temp: "hot" | "warm" | "cold";
};

export const calls: CallItem[] = [
  { name: "مریم رضایی", topic: "خرید آپارتمان ۱۲۰ متری — سعادت‌آباد", time: "۱۰:۳۰", status: "done", temp: "hot" },
  { name: "علی محمدی", topic: "رهن کامل، ۲ خواب — پونک", time: "۱۱:۰۰", status: "missed", temp: "warm" },
  { name: "خانوادهٔ کریمی", topic: "هماهنگی بازدید دوم — ونک", time: "۱۲:۱۵", status: "due", temp: "hot" },
  { name: "نیما جعفری", topic: "فروش واحد ۸۵ متری — شهرک غرب", time: "۱۴:۰۰", status: "due", temp: "warm" },
  { name: "سپیده نوری", topic: "اجاره دفتر اداری — میرداماد", time: "۱۵:۳۰", status: "later", temp: "cold" },
];

export const matches = [
  { customer: "علی محمدی", property: "۱۰۵ متر، ۲ خواب، پارکینگ — پونک", price: 9_800_000_000, score: 92, reasons: ["بودجه", "محله", "پارکینگ"] },
  { customer: "مریم رضایی", property: "۱۱۸ متر، نوساز، آسانسور — سعادت‌آباد", price: 16_500_000_000, score: 87, reasons: ["متراژ", "نوساز"] },
  { customer: "حسین تقوی", property: "۷۰ متر، رهن ۲ میلیارد — تهرانپارس", price: 2_000_000_000, score: 81, reasons: ["رهن", "نزدیک مترو"] },
];

export const agenda = [
  { time: "۱۲:۱۵", title: "بازدید آپارتمان ونک", who: "خانوادهٔ کریمی", kind: "visit" as const },
  { time: "۱۴:۰۰", title: "امضای قرارداد اجاره", who: "آقای صالحی و خانم فرهادی", kind: "contract" as const },
  { time: "۱۶:۳۰", title: "عکاسی از ملک تازه — شهرک غرب", who: "سارا احمدی", kind: "task" as const },
  { time: "فردا ۱۰:۰۰", title: "جلسهٔ هفتگی تیم", who: "همهٔ مشاوران", kind: "meeting" as const },
];

export const districts = [
  { name: "سعادت‌آباد", count: 84, ppm: 142, delta: 2.1 },
  { name: "پونک", count: 71, ppm: 96, delta: -0.8 },
  { name: "ونک", count: 58, ppm: 168, delta: 1.4 },
  { name: "شهرک غرب", count: 52, ppm: 155, delta: 0.6 },
  { name: "تهرانپارس", count: 47, ppm: 74, delta: -1.9 },
  { name: "میرداماد", count: 33, ppm: 181, delta: 3.2 },
];

export const team = [
  { name: "سارا احمدی", role: "مشاور ارشد", calls: 42, visits: 9, deals: 3, response: 8, status: "available" as const },
  { name: "رضا کاظمی", role: "مشاور", calls: 37, visits: 6, deals: 2, response: 12, status: "busy" as const },
  { name: "نگار یوسفی", role: "مشاور", calls: 31, visits: 7, deals: 2, response: 9, status: "available" as const },
  { name: "امید حسینی", role: "کارشناس فایل", calls: 18, visits: 3, deals: 1, response: 21, status: "away" as const },
];

export const system = {
  scraper: { running: 2, queued: 1, progress: 64, lastRun: "۱۲ دقیقه پیش" },
  divar: { healthy: 3, total: 4 },
  backup: { last: "۲ ساعت پیش", ok: true },
  ai: { used: 32 },
};

export const activity = [
  { who: "سارا احمدی", what: "قرارداد اجاره را ثبت کرد", target: "واحد ۴، شهرک غرب", when: "۵ دقیقه پیش", kind: "deal" as const },
  { who: "اسکرپر", what: "۳۸ آگهی تازه آورد", target: "آپارتمان فروش، تهران", when: "۱۲ دقیقه پیش", kind: "scrape" as const },
  { who: "رضا کاظمی", what: "با مشتری تماس گرفت", target: "علی محمدی", when: "۲۶ دقیقه پیش", kind: "call" as const },
  { who: "موتور تطبیق", what: "۲ تطبیق تازه پیدا کرد", target: "برای مریم رضایی", when: "۴۰ دقیقه پیش", kind: "match" as const },
  { who: "نگار یوسفی", what: "یک لید را به مشتری تبدیل کرد", target: "سپیده نوری", when: "۱ ساعت پیش", kind: "lead" as const },
];

export const navGroups = [
  {
    label: "کار روزانه",
    items: [
      { key: "dashboard", label: "داشبورد" },
      { key: "properties", label: "لیست املاک" },
      { key: "crm", label: "CRM و لیدها", badge: 7 },
      { key: "portal", label: "درخواست‌های مشتریان", badge: 2 },
    ],
  },
  {
    label: "اسکرپ و داده",
    items: [
      { key: "scraper", label: "اسکرپر" },
      { key: "divar", label: "احراز هویت دیوار" },
      { key: "proxies", label: "پراکسی‌ها" },
      { key: "insights", label: "هوش تصویری" },
    ],
  },
  {
    label: "ارتباطات",
    items: [
      { key: "sms", label: "پیامک" },
      { key: "email", label: "ایمیل" },
      { key: "forwarder", label: "فرستندهٔ پیامک" },
    ],
  },
  {
    label: "سیستم",
    items: [
      { key: "ai", label: "هوش مصنوعی" },
      { key: "monitoring", label: "پایش سامانه" },
      { key: "users", label: "کاربران و بکاپ" },
      { key: "audit", label: "رویدادها" },
    ],
  },
] as const;

export type NavKey = (typeof navGroups)[number]["items"][number]["key"];

export const me = { name: "سبحان عظیم‌زاده", role: "مدیر کل", initials: "س" };

export const leadSources = [
  { label: "دیوار", value: 268, color: "var(--chart-1)" },
  { label: "پورتال مشتریان", value: 79, color: "var(--chart-2)" },
  { label: "معرفی مشتری قبلی", value: 51, color: "var(--chart-3)" },
  { label: "تماس مستقیم", value: 41, color: "var(--chart-5)" },
  { label: "اینستاگرام", value: 23, color: "var(--chart-4)" },
];

// Calls by weekday (Sat..Fri) and hour (8..19): busy late morning and early
// evening, quiet on Friday.
export const callGrid = Array.from({ length: 7 }, (_, d) =>
  Array.from({ length: 12 }, (_, h) => {
    const hour = 8 + h;
    const peak = Math.exp(-((hour - 11) ** 2) / 3) * 9 + Math.exp(-((hour - 18) ** 2) / 2.5) * 7;
    const day = d === 6 ? 0.25 : d === 5 ? 0.6 : 1;
    return Math.round((peak + 1.5 + r() * 2.5) * day);
  }),
);

export const dealsByMonth = [
  { month: "فروردین", sale: 3, rent: 5, presale: 1 },
  { month: "اردیبهشت", sale: 4, rent: 6, presale: 1 },
  { month: "خرداد", sale: 5, rent: 7, presale: 2 },
  { month: "تیر", sale: 3, rent: 8, presale: 1 },
  { month: "مرداد", sale: 6, rent: 9, presale: 2 },
  { month: "شهریور", sale: 7, rent: 8, presale: 3 },
];

export const teamRadar = [
  { metric: "تماس", best: 92, avg: 68 },
  { metric: "بازدید", best: 84, avg: 61 },
  { metric: "قرارداد", best: 88, avg: 55 },
  { metric: "سرعت پاسخ", best: 90, avg: 64 },
  { metric: "پیگیری", best: 76, avg: 70 },
];

export const targets = [
  { label: "قرارداد", value: 9, target: 12, unit: "", color: "var(--chart-1)" },
  { label: "کمیسیون", value: 480, target: 650, unit: "میلیون", color: "var(--chart-3)" },
];
