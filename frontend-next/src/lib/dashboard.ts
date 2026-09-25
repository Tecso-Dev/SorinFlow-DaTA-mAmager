"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

/** GET /api/stats/overview — app/api/routes/stats.py dashboard_overview. */
export type Overview = {
  generated_at: string;
  days: number;
  scope: "office" | "self";
  kpis: {
    listings_today: number;
    listings_yesterday: number;
    leads_open: number;
    leads_new: number;
    leads_new_before: number;
    hot_customers: number;
    calls_today: number;
    calls_yesterday: number;
    calls_due: number;
    deals: number;
    deals_before: number;
    commission: number;
    conversion: number | null;
    conversion_before: number | null;
  };
  trend: { date: string; listings: number; leads: number; deals: number }[];
  funnel: { key: string; label: string; count: number }[];
  call_grid: { hours: number[]; rows: number[][] };
  team: {
    id: number;
    name: string;
    role: string;
    presence: "available" | "busy" | "away";
    calls: number;
    answered: number;
    visits: number;
    won: number;
    answer_rate: number | null;
  }[];
  deals_by_month: { date: string; type: string; count: number }[];
  districts: { city: string | null; items: { name: string; count: number; ppm: number | null; delta: number | null }[] };
  sources: { key: string; count: number }[];
  target: {
    month_start: string;
    deals: number;
    commission: number;
    deals_target: number | null;
    commission_target: number | null;
    can_edit: boolean;
  };
};

export const OVERVIEW_KEY = (days: number) => ["stats", "overview", days] as const;

export function useOverview(days: number, enabled = true) {
  return useQuery({
    queryKey: OVERVIEW_KEY(days),
    queryFn: () => api<Overview>(`/stats/overview?days=${days}`),
    enabled,
    // the old dashboard refreshed every minute; so does this one
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });
}

/** Percent change, null when there is nothing to compare with. */
export function change(now: number | null | undefined, before: number | null | undefined): number | null {
  if (now == null || !before) return null;
  return Math.round(((now - before) / before) * 1000) / 10;
}

/** GET /api/stats/health */
export type Health = { status: string; database: string; redis: string; scraper: string; cookie_status: string };

/** GET /api/stats/jobs-summary */
export type JobsSummary = {
  by_status: Record<string, number>;
  recent_jobs: { id: number; status: string; started_at: string | null; completed_at: string | null; created_at: string | null; new_items: number | null; progress: number | null }[];
};

/** GET /api/crm/calls/today */
export type CallsToday = {
  items: { id: number; seller_name: string | null; property_title: string | null; city_name: string | null; next_call_at: string | null; call_attempts: number; last_call_outcome: string | null }[];
  total: number;
  done_today: number;
};

/** GET /api/crm/matches */
export type Matches = {
  items: {
    id: number;
    score: number;
    reasons: string[];
    property: { id: number; title: string | null; district: string | null; area: number | null; price: number | null; url: string | null };
    customer: { id: number; full_name: string | null; temperature: string | null };
  }[];
  total: number;
};

/** GET /api/crm/calendar/upcoming */
export type Upcoming = {
  items: { id: number | string; title: string; event_type?: string; kind?: string; start_at: string; customer_name?: string | null; owner_name?: string | null; assigned_to?: string | null; type_label?: string }[];
  total: number;
};

/** GET /api/properties */
export type PropertyPage = {
  items: { id: number; title: string | null; city_name: string | null; district?: string | null; created_at: string | null; scraped_at?: string | null; serial_no?: number | null }[];
  total: number;
};

/** GET /api/crm/leads */
export type LeadPage = {
  items: { id: number; seller_name: string | null; property_title: string | null; status: string; assigned_to: string | null; created_at: string | null }[];
  total: number;
};
