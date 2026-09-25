// Jalali (Solar Hijri) dates, without a library.
//
// Reading a date as Jalali uses the browser's own Persian calendar (Intl);
// the other way, Jalali → Date, is the classic arithmetic (the same one
// app/services/dpa_service.to_jalali uses in reverse). Wall-clock times are
// the device's own, which for this office is Tehran.

export type JDate = { y: number; m: number; d: number };

const PARTS = new Intl.DateTimeFormat("en-u-ca-persian-nu-latn", { year: "numeric", month: "numeric", day: "numeric" });

/** The Jalali year, month (1-12) and day of `date`. */
export function jParts(date: Date): JDate {
  const p = Object.fromEntries(PARTS.formatToParts(date).map((x) => [x.type, x.value]));
  return { y: Number(p.year), m: Number(p.month), d: Number(p.day) };
}

function div(a: number, b: number) {
  return Math.floor(a / b);
}

/** Jalali → Gregorian [year, month (1-12), day]. */
export function toGregorian(jy: number, jm: number, jd: number): [number, number, number] {
  jy += 1595;
  let days = -355668 + 365 * jy + div(jy, 33) * 8 + div((jy % 33) + 3, 4) + jd + (jm < 7 ? (jm - 1) * 31 : (jm - 7) * 30 + 186);
  let gy = 400 * div(days, 146097);
  days %= 146097;
  if (days > 36524) {
    gy += 100 * div(--days, 36524);
    days %= 36524;
    if (days >= 365) days++;
  }
  gy += 4 * div(days, 1461);
  days %= 1461;
  if (days > 365) {
    gy += div(days - 1, 365);
    days = (days - 1) % 365;
  }
  let gd = days + 1;
  const leap = (gy % 4 === 0 && gy % 100 !== 0) || gy % 400 === 0;
  const months = [0, 31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  let gm = 1;
  while (gm <= 12 && gd > months[gm]) {
    gd -= months[gm];
    gm++;
  }
  return [gy, gm, gd];
}

/** A local Date at the given Jalali day and wall-clock time. */
export function fromJalali(y: number, m: number, d: number, h = 0, min = 0): Date {
  const [gy, gm, gd] = toGregorian(y, m, d);
  return new Date(gy, gm - 1, gd, h, min, 0, 0);
}

/** Days in a Jalali month (Esfand is 30 in a leap year). */
export function monthLength(y: number, m: number): number {
  if (m <= 6) return 31;
  if (m <= 11) return 30;
  return jParts(fromJalali(y, 12, 30)).m === 12 ? 30 : 29;
}

export const MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"];
/** Saturday first. */
export const WEEKDAYS = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"];
export const WEEKDAYS_SHORT = ["ش", "ی", "د", "س", "چ", "پ", "ج"];

/** 0 = Saturday … 6 = Friday. */
export const weekdaySat0 = (date: Date) => (date.getDay() + 1) % 7;

const toLatin = (s: string) =>
  s.replace(/[۰-۹]/g, (c) => String(c.charCodeAt(0) - 0x06f0)).replace(/[٠-٩]/g, (c) => String(c.charCodeAt(0) - 0x0660));

/**
 * «1405/07/03», «۱۴۰۵-۷-۳ ۱۴:۳۰» → a Date, or null when it is not a real
 * Jalali date. Persian or Arabic digits, / or - between the parts.
 */
export function parseJalali(input: string): Date | null {
  const m = toLatin(input.trim()).match(/^(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?$/);
  if (!m) return null;
  const [y, mo, d, h = "0", mi = "0"] = m.slice(1).map((x) => x ?? "0");
  const Y = Number(y), M = Number(mo), D = Number(d), H = Number(h), MI = Number(mi);
  if (M < 1 || M > 12 || D < 1 || D > monthLength(Y, M) || H > 23 || MI > 59) return null;
  return fromJalali(Y, M, D, H, MI);
}

const pad = (n: number) => String(n).padStart(2, "0");

/** «1405/07/03» (or with « 14:30»), latin digits: what a date input holds. */
export function formatJalali(date: Date, withTime = false): string {
  const { y, m, d } = jParts(date);
  const day = `${y}/${pad(m)}/${pad(d)}`;
  return withTime ? `${day} ${pad(date.getHours())}:${pad(date.getMinutes())}` : day;
}

/** The first day of the Jalali month `date` is in. */
export function startOfJMonth(date: Date): Date {
  const { y, m } = jParts(date);
  return fromJalali(y, m, 1);
}

export function addJMonths(date: Date, n: number): Date {
  const { y, m } = jParts(date);
  const t = y * 12 + (m - 1) + n;
  return fromJalali(Math.floor(t / 12), (t % 12) + 1, 1);
}

/** «2026-09-25» for a local date — the form the backend's date filters take. */
export function isoDay(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}
