// The insights page's own formatting rules (app.js _insNum/_insPct/_insToman,
// app/services/crm_insights.py's "never invent a number" comment): a value
// the server could not compute is null, and null renders «—», never a
// synthetic 0 — a real zero and "cannot say yet" must stay visually distinct
// everywhere on this page.

import { faNum, faPercent } from "@/lib/format";

export function insNum(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : faNum(v);
}

export function insPct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : faPercent(v, 1);
}

/** ≥1e9 → "X میلیارد" (1 decimal); ≥1e6 → "X میلیون" (0 decimals); else the
 *  plain localized number — no «تومان» suffix, unlike lib/crm's price(). */
export function insToman(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (v >= 1e9) return `${faNum(v / 1e9, { maximumFractionDigits: 1 })} میلیارد`;
  if (v >= 1e6) return `${faNum(v / 1e6, { maximumFractionDigits: 0 })} میلیون`;
  return faNum(v);
}
