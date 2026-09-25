// The shapes GET /properties/visual/overview and GET /crm/insights answer
// with (app/api/routes/properties.py:visual_overview, app/api/routes/crm.py:
// crm_insights, app/services/crm_insights.py). Only the fields the two tabs
// read are typed.

export type ValuationItem = {
  id: number;
  serial_no: number | null;
  title: string;
  district: string | null;
  city_name: string | null;
  area: number | null;
  ppm: number | null;
  median_ppm: number | null;
  delta_pct: number;
  sample: number;
  confidence: "thin" | "ok" | string;
  note: string;
};

export type DuplicatePairSide = { id: number; serial_no: number | null; title: string; price: number | null; seller: string | null };
export type DuplicatePair = { verdict: "duplicate" | "maybe"; note: string; shared_images: number; a: DuplicatePairSide; b: DuplicatePairSide };

export type WeakPhoto = { id: number; serial_no: number | null; title: string; count: number | null; worst: number | null; problems: string[] };

export type VisualOverview = {
  generated_at: string;
  valuation: {
    judged: number;
    total: number;
    districts_with_a_benchmark: number;
    districts_seen: number;
    min_comparables: number;
    under: ValuationItem[];
    over: ValuationItem[];
  };
  duplicates: { pairs: DuplicatePair[]; total_pairs: number; with_hashes: number; boilerplate_ignored: number };
  photos: { scored_listings: number; problems: Record<string, number>; weak: WeakPhoto[] };
};

export type FunnelStage = { key: string; label: string; count: number; unexpected?: boolean };

export type StalledLead = { id: number; seller_name: string | null; phone_number: string | null; city_name: string | null; status: string; status_label: string; idle_days: number };

export type AgentRow = { agent: string; days: number; new_files: number; showings: number; offers: number; closed: number; showings_per_close: number | null };

export type Bucket = { label: string; count: number; is_other?: boolean };

export type CrmInsights = {
  generated_at: string;
  window_days: number;
  funnel: FunnelStage[];
  totals: { leads: number; properties: number; customers: number; conversion_rate: number | null };
  stalled: { after_days: number; items: StalledLead[] };
  series: { leads: { date: string; count: number }[]; properties: { date: string; count: number }[] };
  cities: Bucket[];
  temperature: Bucket[];
  agents: AgentRow[];
  deals: { deal_count: number; open_count: number; closed_count: number; total_amount: number; closed_amount: number; commission_paid: number; commission_due: number };
  coverage: { properties_with_phone: number | null; leads_with_phone: number | null };
};
