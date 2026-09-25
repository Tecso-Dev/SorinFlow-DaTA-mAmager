// Shapes the scraper section reads from and writes to the backend
// (app/api/routes/scraper.py + app/schemas.ScrapingJob*). Kept in one place
// so the form, the jobs table and the dialogs agree on one vocabulary.

export type City = { slug: string; name: string; province: string };
export type Category = { slug: string; name: string; type: "buy" | "rent" | "service" };

/** GET /auth/cookies?mine=1 — the Divar numbers this user may use. */
export type DivarCookie = {
  id: number;
  phone_number: string;
  is_valid: boolean;
  expires_at: string | null;
  last_checked_at: string | null;
  created_at: string | null;
  reveals: number;
  challenged_at: string | null;
  last_used_at: string | null;
  owner_user_id: number | null;
  identity_required_at: string | null;
  is_enabled: boolean;
  owner_name: string | null;
};
export type CookiesResponse = { sees_every_session: boolean; cookies: DivarCookie[] };

export const divarUsable = (c: DivarCookie | null | undefined): boolean =>
  !!c && c.is_valid && c.is_enabled !== false && !c.identity_required_at;

export type AdvertiserType = "personal" | "agency";

/** POST /scraper/start body (ScrapingJobCreate), minus urls/divar_phone which
 *  the form and the single/rescrape flows attach on their own. */
export type ScrapeFilters = {
  min_price?: number;
  max_price?: number;
  min_deposit?: number;
  max_deposit?: number;
  min_rent?: number;
  max_rent?: number;
  min_price_per_meter?: number;
  max_price_per_meter?: number;
  min_area?: number;
  max_area?: number;
  min_rooms?: number;
  max_rooms?: number;
  has_images?: boolean;
  has_elevator?: boolean;
  has_parking?: boolean;
  has_storage?: boolean;
  has_balcony?: boolean;
  advertiser_type?: AdvertiserType;
  posted_date?: string; // Gregorian YYYY-MM-DD
  rotate_every?: number;
};

export type ScrapeConfig = ScrapeFilters & {
  city: string;
  category: string;
  urls?: string[];
  max_items?: number;
  download_images: boolean;
  divar_phone?: string;
  max_age_hours?: number;
};

export type JobStatus = "pending" | "running" | "paused" | "completed" | "failed" | "cancelled";

/** ScrapingJobResponse. */
export type ScrapeJob = {
  id: number;
  job_id: string;
  city_id: number | null;
  category_id: number | null;
  city_name: string | null;
  category_name: string | null;
  divar_phone: string | null;
  accounts_used: string[];
  owner_user_id: number | null;
  owner_name: string | null;
  status: JobStatus;
  total_pages: number;
  scraped_pages: number;
  total_items: number;
  scraped_items: number;
  new_items: number;
  updated_items: number;
  failed_items: number;
  error_message: string | null;
  finish_reason: string | null;
  progress: number;
  divar_count: number | null;
  resumed_from: string | null;
  can_resume: boolean;
  started_at: string | null;
  completed_at: string | null;
  created_at: string | null;
};

export type JobList = { items: ScrapeJob[]; total: number };

export type LastResult = { status?: string; detail?: string } | null;

/** _schedule_view — GET/POST/PATCH /scraper/schedules. */
export type Schedule = {
  id: number;
  name: string;
  config: ScrapeConfig;
  hour: number;
  minute: number;
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  last_result: LastResult;
  owner_name: string | null;
  city_name: string | null;
  category_name: string | null;
};

export type SchedulesResponse = { schedules: Schedule[]; can_see_all: boolean };

/** GET /scraper/estimate */
export type EstimateResponse = {
  count: number | null;
  error: string | null;
  applied_by_divar: string[];
  applied_after_scrape: string[];
};

/** POST /scraper/parse-link */
export type ParseLinkResponse = {
  city: string | null;
  city_name: string | null;
  category: string | null;
  category_name: string | null;
  filters: Record<string, unknown>;
  ignored: string[];
};

export type JobEvent = {
  id: number;
  level: string;
  stage: string | null;
  message: string;
  details: Record<string, unknown>;
  created_at: string | null;
};
export type JobEventsResponse = { job_id: string; count: number; items: JobEvent[] };

export type SkippedItem = {
  id: number;
  divar_id: string | null;
  url: string;
  title: string | null;
  reason: string;
  reason_label: string;
  detail: string | null;
  created_at: string | null;
};
export type SkippedResponse = {
  job_id: string;
  count: number;
  by_reason: Record<string, { label: string; count: number }>;
  items: SkippedItem[];
};

export const JOB_STATUS_FA: Record<JobStatus, string> = {
  pending: "در صف",
  running: "در حال اجرا",
  paused: "متوقف — منتظر کد",
  completed: "تکمیل شده",
  failed: "ناموفق",
  cancelled: "لغو شده",
};

export const JOB_STATUS_TONE: Record<JobStatus, "neutral" | "info" | "warning" | "success" | "danger" | "primary"> = {
  pending: "neutral",
  running: "info",
  paused: "warning",
  completed: "success",
  failed: "danger",
  cancelled: "neutral",
};
