"use client";

// The «اسکرپینگ جدید» form's state, and the config object it turns into —
// exactly the shape POST /scraper/start, POST /scraper/schedules and
// GET /scraper/estimate all want (ScrapingJobCreate minus the parts that
// differ per action). One hook so the estimate, the start button and «هر
// روز خودکار اجرا شود» never drift from what is actually on screen.

import { useMemo, useState } from "react";
import { parseDigits } from "@/lib/format";
import { isoDay } from "@/lib/jalali";
import type { AdvertiserType, ScrapeConfig } from "./types";

export type ScrapeFormState = {
  city: string;
  category: string;
  account: string; // "" = خودکار — کم‌مصرف‌ترین
  minPrice: string;
  maxPrice: string;
  minPricePerMeter: string;
  maxPricePerMeter: string;
  minDeposit: string;
  maxDeposit: string;
  minRent: string;
  maxRent: string;
  minArea: string;
  maxArea: string;
  minRooms: string;
  maxRooms: string;
  advertiserType: "" | AdvertiserType;
  hasImages: boolean;
  hasElevator: boolean;
  hasParking: boolean;
  hasStorage: boolean;
  hasBalcony: boolean;
  postedDate: Date | null;
  pages: string;
  rotateEvery: string;
  downloadImages: boolean;
};

export const EMPTY_SCRAPE_FORM: ScrapeFormState = {
  city: "",
  category: "",
  account: "",
  minPrice: "",
  maxPrice: "",
  minPricePerMeter: "",
  maxPricePerMeter: "",
  minDeposit: "",
  maxDeposit: "",
  minRent: "",
  maxRent: "",
  minArea: "",
  maxArea: "",
  minRooms: "",
  maxRooms: "",
  advertiserType: "",
  hasImages: false,
  hasElevator: false,
  hasParking: false,
  hasStorage: false,
  hasBalcony: false,
  postedDate: null,
  pages: "",
  rotateEvery: "",
  downloadImages: true,
};

function toNumber(s: string): number | undefined {
  const digits = parseDigits(s).replace(/[^\d]/g, "").trim();
  return digits ? Number(digits) : undefined;
}

/** The filters alone, as query params for GET /scraper/estimate (no
 *  city/category — the caller adds those; estimate takes city required,
 *  category optional). A plain function of `state` so a debounced copy of
 *  the form can be turned into the same params the live form would send. */
export function filtersToParams(state: ScrapeFormState) {
  return {
    advertiser_type: state.advertiserType || undefined,
    has_images: state.hasImages || undefined,
    min_price: toNumber(state.minPrice),
    max_price: toNumber(state.maxPrice),
    min_deposit: toNumber(state.minDeposit),
    max_deposit: toNumber(state.maxDeposit),
    min_rent: toNumber(state.minRent),
    max_rent: toNumber(state.maxRent),
    min_area: toNumber(state.minArea),
    max_area: toNumber(state.maxArea),
    min_rooms: toNumber(state.minRooms),
    max_rooms: toNumber(state.maxRooms),
    has_elevator: state.hasElevator || undefined,
    has_parking: state.hasParking || undefined,
    has_storage: state.hasStorage || undefined,
    min_price_per_meter: toNumber(state.minPricePerMeter),
    max_price_per_meter: toNumber(state.maxPricePerMeter),
  };
}

export function useScrapeForm() {
  const [state, setState] = useState<ScrapeFormState>(EMPTY_SCRAPE_FORM);

  function set<K extends keyof ScrapeFormState>(key: K, value: ScrapeFormState[K]) {
    setState((s) => ({ ...s, [key]: value }));
  }

  /** How many of the «فیلترهای بیشتر» are actually set — the collapsible
   *  opens itself and shows this count when it is closed. */
  const activeFilterCount = useMemo(() => {
    let n = 0;
    if (state.minPrice || state.maxPrice) n++;
    if (state.minPricePerMeter || state.maxPricePerMeter) n++;
    if (state.minDeposit || state.maxDeposit) n++;
    if (state.minRent || state.maxRent) n++;
    if (state.minArea || state.maxArea) n++;
    if (state.minRooms || state.maxRooms) n++;
    if (state.advertiserType) n++;
    if (state.hasImages || state.hasElevator || state.hasParking || state.hasStorage || state.hasBalcony) n++;
    if (state.postedDate) n++;
    return n;
  }, [state]);

  const estimateParams = useMemo(() => filtersToParams(state), [state]);

  /** The full body for /scraper/start (or the config a schedule saves —
   *  same shape, the schedule just omits the picked account when it is
   *  «خودکار»). Empty pages + no posted date = leave max_items unset, so
   *  the server's own default (100) applies. */
  function buildConfig(): ScrapeConfig {
    const pagesN = toNumber(state.pages);
    const cfg: ScrapeConfig = {
      city: state.city,
      category: state.category,
      download_images: state.downloadImages,
      ...estimateParams,
      rotate_every: toNumber(state.rotateEvery),
    };
    if (pagesN && pagesN > 0) cfg.max_items = pagesN;
    if (state.postedDate) cfg.posted_date = isoDay(state.postedDate);
    if (state.account) cfg.divar_phone = state.account;
    return Object.fromEntries(Object.entries(cfg).filter(([, v]) => v !== undefined)) as ScrapeConfig;
  }

  /** A parsed link's answer, written into the form. Read-only by design —
   *  it never starts anything on its own. */
  function applyParsed(city: string | null, category: string | null, filters: Record<string, unknown>) {
    setState((s) => ({
      ...s,
      city: city ?? s.city,
      category: category ?? s.category,
      minPrice: filters.min_price != null ? String(filters.min_price) : "",
      maxPrice: filters.max_price != null ? String(filters.max_price) : "",
      minPricePerMeter: filters.min_price_per_meter != null ? String(filters.min_price_per_meter) : "",
      maxPricePerMeter: filters.max_price_per_meter != null ? String(filters.max_price_per_meter) : "",
      minDeposit: filters.min_deposit != null ? String(filters.min_deposit) : "",
      maxDeposit: filters.max_deposit != null ? String(filters.max_deposit) : "",
      minRent: filters.min_rent != null ? String(filters.min_rent) : "",
      maxRent: filters.max_rent != null ? String(filters.max_rent) : "",
      minArea: filters.min_area != null ? String(filters.min_area) : "",
      maxArea: filters.max_area != null ? String(filters.max_area) : "",
      minRooms: filters.min_rooms != null ? String(filters.min_rooms) : "",
      maxRooms: filters.max_rooms != null ? String(filters.max_rooms) : "",
      advertiserType: (filters.advertiser_type as "" | AdvertiserType) || "",
      hasImages: !!filters.has_images,
    }));
  }

  return { state, set, setState, activeFilterCount, buildConfig, applyParsed, estimateParams, toNumber };
}
