// Persian display helpers. Everything the user reads goes through these, so
// digits, separators and the Jalali calendar are the same on every page.

const LOCALE = "fa-IR-u-ca-persian-nu-arabext";

export function faNum(n: number, opts?: Intl.NumberFormatOptions): string {
  return new Intl.NumberFormat(LOCALE, opts).format(n);
}

export function faPercent(n: number, digits = 1): string {
  return `${faNum(n, { maximumFractionDigits: digits })}٪`;
}

/** 480_000_000 → «۴۸۰ میلیون», 12_500_000_000 → «۱۲٫۵ میلیارد» (toman). */
export function toman(n: number, withUnit = true): string {
  const unit = withUnit ? " تومان" : "";
  if (n >= 1e9) return `${faNum(n / 1e9, { maximumFractionDigits: 1 })} میلیارد${unit}`;
  if (n >= 1e6) return `${faNum(n / 1e6, { maximumFractionDigits: 1 })} میلیون${unit}`;
  return `${faNum(n)}${unit}`;
}

export function faDate(d: Date, opts: Intl.DateTimeFormatOptions): string {
  return new Intl.DateTimeFormat(LOCALE, opts).format(d);
}

/** Western digits typed by the user (or pasted from Divar) → numbers. */
export function parseDigits(s: string): string {
  return s
    .replace(/[۰-۹]/g, (c) => String(c.charCodeAt(0) - 0x06f0))
    .replace(/[٠-٩]/g, (c) => String(c.charCodeAt(0) - 0x0660));
}
