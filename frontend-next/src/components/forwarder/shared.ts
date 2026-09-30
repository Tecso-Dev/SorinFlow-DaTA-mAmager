// Small vocabulary shared by the forwarder's pieces: no logic, just labels
// and tones so a state never gets described two different ways.

import type { Tone } from "@/lib/crm";
import type { HealthState } from "./types";

/** app.js's FW_STATE, kept in the same order the health machine reports. */
export const HEALTH_TONE: Record<HealthState, Tone> = {
  ok: "success",
  no_codes_yet: "info",
  offline: "warning",
  never_seen: "neutral",
  disabled: "danger",
};

/** app.js's FW_REASON — details.reason on an inbound sms_event. */
export const FW_REASON: Record<string, string> = {
  matched: "به اسکرپر داده شد",
  parked_early: "زودتر رسید — نگه داشته شد",
  stale_code: "کد قدیمی بود",
  no_code_in_text: "کدی در متن پیدا نشد",
  no_pending_for_account: "منتظر کدی برای این شماره نبود",
  already_answered: "قبلاً پاسخ داده شده بود",
  test: "پیام آزمایشی",
};

export type LogFilter = "all" | "matched" | "parked_early" | "problem";

/** Which bucket a reason falls in for the log's filter chips. Anything not
 *  matched/parked is «مشکل‌دار» — stale, unmatched or already-answered are
 *  all things worth a second look. */
export function bucketOf(reason: string | undefined): LogFilter {
  if (reason === "matched") return "matched";
  if (reason === "parked_early") return "parked_early";
  return "problem";
}
