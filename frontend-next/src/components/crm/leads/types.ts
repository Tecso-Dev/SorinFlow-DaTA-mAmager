// The shapes the leads and call-queue routes answer with (app/api/routes/crm.py,
// app/schemas LeadResponse). Only the fields the panel reads are typed.

export type PropertyDetail = {
  id: number;
  serial_no: number | null;
  title: string | null;
  description: string | null;
  url: string | null;
  images: string[] | null;
  area: number | null;
  land_area: number | null;
  built_area: number | null;
  rooms: number | null;
  floor: number | null;
  total_floors: number | null;
  year_built: number | null;
  building_age: string | null;
  building_direction: string | null;
  corner_type: string | null;
  frontage: number | null;
  unit_status: string | null;
  document_type: string | null;
  usage_type: string | null;
  property_type: string | null;
  category_name: string | null;
  listing_type: string | null;
  advertiser_type: string | null;
  agency_suspected: boolean | null;
  agency_evidence: string | null;
  ai_duplicate_of: number | null;
  total_price: number | null;
  price: number | null;
  price_per_meter: number | null;
  deposit: number | null;
  rent_price: number | null;
  has_elevator: boolean | null;
  has_parking: boolean | null;
  has_storage: boolean | null;
  has_balcony: boolean | null;
  city_name: string | null;
  district: string | null;
  neighborhood: string | null;
  address: string | null;
  latitude: number | null;
  longitude: number | null;
  phone_number: string | null;
  seller_name: string | null;
  extra_attrs: Record<string, unknown> | null;
};

export type Lead = {
  id: number;
  property_id: number;
  phone_number: string | null;
  seller_name: string | null;
  city_name: string | null;
  category_name: string | null;
  listing_type: string | null;
  price: number | null;
  area: number | null;
  property_url: string | null;
  property_title: string | null;
  status: string;
  notes: string | null;
  assigned_to: string | null;
  assigned_to_user_id: number | null;
  next_call_at: string | null;
  call_attempts: number | null;
  last_call_at: string | null;
  last_call_outcome: string | null;
  notified: boolean;
  notified_at: string | null;
  notification_channel: string | null;
  created_at: string | null;
  updated_at: string | null;
  district: string | null;
  serial_no: number | null;
  scraped_at: string | null;
  price_per_meter: number | null;
  document_type: string | null;
  has_parking: boolean | null;
  has_elevator: boolean | null;
  building_direction: string | null;
  corner_type: string | null;
  lead_advertiser_type: string | null;
  contact_channel: string | null;
  agency_suspected: boolean | null;
  agency_evidence: string | null;
  ai_duplicate_of: number | null;
  property_detail: PropertyDetail | null;
};

export type LeadPage = { items: Lead[]; total: number };

export type Activity = { id: number; action: string; detail: string | null; actor: string | null; created_at: string | null };

/** A listing from the match engine (GET /crm/match/lead|property, /ai/embed/search). */
export type MatchedListing = {
  id: number;
  serial_no: number | null;
  title: string;
  city_name: string | null;
  district: string | null;
  area: number | null;
  rooms: number | null;
  listing_type: string | null;
  price: number | null;
  deposit: number | null;
  rent_price: number | null;
  comparable: number | null;
  url: string | null;
  phone_number: string | null;
  score: number;
  reasons: string[] | null;
  ai_reason?: string | null;
  same_district?: boolean;
  price_gap_pct?: number | null;
  price_direction?: "higher" | "lower" | null;
  similarity?: number;
  ai_duplicate_of?: number | null;
};

/** A customer from the reverse match (GET /crm/match/property/{id}/customers). */
export type MatchedCustomer = {
  id: number;
  full_name: string;
  mobile1: string | null;
  mobile2: string | null;
  temperature: string | null;
  desired_district: string | null;
  desired_specs: string | null;
  consultant_name: string | null;
  budget_max: number | null;
  score: number;
  reasons: string[] | null;
  ai_reason?: string | null;
};

export type MatchSource = {
  id?: number;
  title?: string;
  name?: string;
  serial_no?: number | null;
  district?: string | null;
  city_name?: string | null;
  listing_type?: string | null;
  price?: number | null;
  deposit?: number | null;
  rent_price?: number | null;
  comparable?: number | null;
};

export type MatchResult<T> = {
  items: T[];
  total?: number;
  source?: MatchSource;
  intent?: { listing_type?: string; family?: string; city?: string } | null;
  reasons_pending?: boolean;
};

export type Cabinet = {
  id: number;
  name: string;
  binders: { id: number; name: string; folders?: { id: number; name: string }[] }[];
};
