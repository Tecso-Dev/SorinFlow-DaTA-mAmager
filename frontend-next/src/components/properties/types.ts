// The shapes the properties routes answer with (app/api/routes/properties.py,
// app/schemas PropertyResponse). Only the fields the panel reads are typed.

export type Property = {
  id: number;
  tag_number: string;
  serial_no: number | null;
  divar_id: string;
  title: string;
  description: string | null;
  url: string;
  price: number | null;
  price_per_meter: number | null;
  total_price: number | null;
  rent_price: number | null;
  deposit: number | null;
  area: number | null;
  land_area: number | null;
  built_area: number | null;
  rooms: number | null;
  year_built: number | null;
  floor: number | null;
  total_floors: number | null;
  has_elevator: boolean;
  has_parking: boolean;
  has_storage: boolean;
  has_balcony: boolean;
  city_name: string | null;
  district: string | null;
  neighborhood: string | null;
  address: string | null;
  latitude: number | null;
  longitude: number | null;
  category_name: string | null;
  property_type: string | null;
  listing_type: string | null;
  corner_type: string | null;
  building_direction: string | null;
  frontage: number | null;
  unit_status: string | null;
  document_type: string | null;
  usage_type: string | null;
  building_age: string | null;
  extra_attrs: Record<string, unknown> | null;
  phone_number: string | null;
  seller_name: string | null;
  owner_phone: string | null;
  images: string[];
  has_images: boolean;
  thumbnail_url: string | null;
  is_active: boolean;
  advertiser_type: string | null;
  agency_suspected: boolean | null;
  agency_evidence: string | null;
  contact_channel: string | null;
  ai_facts: Record<string, unknown> | null;
  ai_read_at: string | null;
  ai_duplicate_of: number | null;
  scraped_at: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type PropertyPage = { items: Property[]; total: number; page: number; size: number; pages: number };

export type City = { slug: string; name: string; province: string };
export type Category = { slug: string; name: string; type: "buy" | "rent" };

/** GET /crm/match/property/{id} — the same shape the match modal reads. */
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
  ai_duplicate_of?: number | null;
};

export type MatchSource = {
  id?: number;
  title?: string;
  serial_no?: number | null;
  district?: string | null;
  city_name?: string | null;
  listing_type?: string | null;
  price?: number | null;
  deposit?: number | null;
  rent_price?: number | null;
  comparable?: number | null;
};

export type MatchResult = {
  items: MatchedListing[];
  total: number;
  source: MatchSource;
  reasons_pending?: boolean;
};

/** GET /ai/photo/{id} — the tag chips shown in the detail sheet (root/super_admin only). */
export type PhotoTags = { id: number; tags: Record<string, unknown> | null; labels: string[]; tagged_at: string | null };

/** GET /ai/photo/status — the «برچسب‌های هوش تصویری» card on the insights page. */
export type PhotoStatus = {
  cursor: number;
  tagged: number;
  skipped: number;
  behind: number;
  capped: number;
  version: number;
  model: string;
  enabled: boolean;
  configured: boolean;
  last_at: string | null;
};

/** app/ai/listing_reader.py ListingFacts — the «برداشت هوش مصنوعی» box. */
export type ListingFacts = {
  kind: string | null;
  floor: number | null;
  total_floors: number | null;
  year_built: number | null;
  document: string | null;
  condition: string | null;
  has_elevator: boolean | null;
  has_parking: boolean | null;
  has_storage: boolean | null;
  has_balcony: boolean | null;
  district: string | null;
  convertible: boolean | null;
  exchange: boolean | null;
  vacant: boolean | null;
  negotiable: boolean | null;
  suitable_for: string[];
  red_flags: string[];
  summary: string;
  confidence: Record<string, number>;
  /** stamped on by listing_reader.reread/tag_property beside the facts themselves */
  model?: string;
  prompt_version?: number;
};
