// Shared vocabulary for کمد و زونکن. Straight off app/api/routes/filing.py
// and app/models/crm_models.py (Cabinet, Binder) — a "file" is a Property row,
// nothing here duplicates property data, only where it lives and how it is marked.

export const CABINET_PALETTE = [
  "#fbbf24", "#34d399", "#38bdf8", "#a78bfa", "#f472b6",
  "#fb923c", "#2dd4bf", "#f87171", "#a3e635", "#e879f9",
];

export const BINDER_KINDS: Record<string, string> = { property: "فایل ملکی", demand: "فایل متقاضی" };
export const DEAL_TYPES: Record<string, string> = {
  buy: "خرید و فروش", rent: "رهن و اجاره", partnership: "مشارکت", presale: "پیش‌فروش", "": "همه",
};

export type Binder = {
  id: number;
  cabinet_id: number;
  parent_id: number | null;
  name: string;
  color: string;
  kind: string;
  kind_label: string;
  deal_type: string;
  deal_label: string;
  description: string | null;
  sort_order: number;
  file_count: number;
  own_count?: number;
  folders?: Binder[];
  created_at: string | null;
};

export type Cabinet = {
  id: number;
  name: string;
  color: string;
  icon: string;
  sort_order: number;
  owner: string | null;
  owner_user_id: number | null;
  binders: Binder[];
  file_count: number;
  created_at: string | null;
};

/** GET /filing/files item — app.filing._file_brief. */
export type FileBrief = {
  id: number;
  serial_no: number | null;
  title: string;
  city_name: string | null;
  district: string | null;
  area: number | null;
  rooms: number | null;
  listing_type: string | null;
  property_type: string | null;
  price: number | null;
  rent_price: number | null;
  deposit: number | null;
  phone_number: string | null;
  seller_name: string | null;
  thumbnail_url: string | null;
  url: string | null;
  binder_id: number | null;
  is_pinned: boolean;
  is_archived: boolean;
  is_private: boolean;
  is_draft: boolean;
  created_by: string | null;
  tags: string[];
  scraped_at: string | null;
};

/** GET/PATCH /filing/files/{id} — the brief plus the edit form's fields. */
export type FileFull = FileBrief & {
  description: string | null;
  address: string | null;
  neighborhood: string | null;
  floor: number | null;
  total_floors: number | null;
  year_built: number | null;
  total_price: number | null;
  price_per_meter: number | null;
  has_elevator: boolean | null;
  has_parking: boolean | null;
  has_storage: boolean | null;
  has_balcony: boolean | null;
  document_type: string | null;
  building_direction: string | null;
  corner_type: string | null;
  unit_status: string | null;
  category_name: string | null;
};

export type Overview = { filed: number; unfiled: number; pinned: number; archived: number; private: number };

export type AdvancedFilters = {
  price_min: string; price_max: string; area_min: string; area_max: string; rooms_min: string;
  district: string; property_type: string; listing_type: string;
  has_elevator: boolean; has_parking: boolean; has_storage: boolean;
};

export const EMPTY_ADVANCED: AdvancedFilters = {
  price_min: "", price_max: "", area_min: "", area_max: "", rooms_min: "",
  district: "", property_type: "", listing_type: "",
  has_elevator: false, has_parking: false, has_storage: false,
};

/** Every folder inside a cabinet's binders, flattened — for the move picker
 *  and for "which folder is this file in" on a card outside the open binder. */
export function allBinders(cabinets: Cabinet[]): Binder[] {
  const out: Binder[] = [];
  for (const c of cabinets) {
    for (const b of c.binders) {
      out.push(b);
      for (const f of b.folders ?? []) out.push(f);
    }
  }
  return out;
}
