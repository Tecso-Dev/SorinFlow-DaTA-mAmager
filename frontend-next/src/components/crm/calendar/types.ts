// Shared vocabulary for the تقویم tab. The rows themselves come straight off
// GET /crm/calendar (CalendarEvent.to_dict / _task_as_event / _reminder_as_event
// in app/api/routes/crm.py) — colour and label ride along on every row, so
// this file only carries what has no backend counterpart: the type picker's
// options and small date helpers the grid needs.

const pad = (n: number) => String(n).padStart(2, "0");

/** A local wall-clock ISO string with no zone suffix — what the calendar
 *  endpoints read as "the office's own time" (see CLAUDE.md's Tehran note). */
export function localIso(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:00`;
}

export function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

export type CalRecipient = { role: string; name: string | null; phone: string };

/** One row of GET /crm/calendar — an editable event, or a read-only task/reminder overlay. */
export type CalendarRow = {
  id: number;
  kind: "event" | "task" | "reminder";
  title: string;
  event_type: string;
  type_label: string;
  color: string;
  description?: string | null;
  start_at: string | null;
  end_at?: string | null;
  all_day: boolean;
  location?: string | null;
  property_id?: number | null;
  property_serial?: number | null;
  lead_id?: number | null;
  customer_id?: number | null;
  contact_id?: number | null;
  deal_id?: number | null;
  owner_name?: string | null;
  owner_phone?: string | null;
  customer_name?: string | null;
  customer_phone?: string | null;
  assigned_to?: string | null;
  agent_phone?: string | null;
  sms_recipients?: CalRecipient[];
  status: "scheduled" | "done" | "canceled" | string;
  outcome?: string | null;
  remind_before?: number | null;
  sms_reminder?: boolean;
  sms_sent?: boolean;
  created_by?: string | null;
  created_at?: string | null;
};

/** CalendarEvent.EVENT_TYPES (app/models/crm_models.py) — appointment types a
 *  person can pick when scheduling; task/reminder overlays never go through here. */
export const EVENT_TYPES: Record<string, { label: string; color: string }> = {
  visit: { label: "بازدید ملک", color: "#34d399" },
  meeting: { label: "نشست و قرارداد", color: "#a78bfa" },
  call: { label: "تماس تلفنی", color: "#38bdf8" },
  showing: { label: "نمایش به مشتری", color: "#fbbf24" },
  personal: { label: "شخصی", color: "#94a3b8" },
  other: { label: "سایر", color: "#f472b6" },
};

export const REMIND_OPTIONS: [number, string][] = [
  [0, "بدون یادآوری"], [15, "۱۵ دقیقه قبل"], [30, "۳۰ دقیقه قبل"],
  [60, "۱ ساعت قبل"], [180, "۳ ساعت قبل"], [1440, "۱ روز قبل"],
];

export const STATUS_LABEL: Record<string, string> = {
  scheduled: "برنامه‌ریزی‌شده", done: "انجام شد", canceled: "لغو شد",
};
