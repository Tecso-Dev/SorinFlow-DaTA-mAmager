# Migration checklist — «لیست املاک» (properties) و «هوش تصویری» (insights)

Source: frontend/index.html, frontend/js/app.js, frontend/js/ai/{photo,reader,embed}.js,
app/api/routes/{properties,crm,ai_photo,ai_reader,ai_embed}.py, app/services/crm_insights.py,
app/schemas/__init__.py, app/models/property.py, app/api/routes/__init__.py (perm wiring).

---

## 0. Navigation / permission wiring (applies to both)

- Sidebar links: `frontend/index.html:423-426` (`nav-link-properties`, "لیست املاک", icon `bi-house-door`,
  `onclick="showSection('properties')"`) and `frontend/index.html:456-459` (`nav-link-insights`,
  "هوش تصویری", icon `bi-graph-up-arrow`, `onclick="showSection('insights')"`, sits in the **اسکرپ و داده** nav-group, not the CRM group).
- `showSection()` — `app.js:1737-1802`. Dispatch: `case 'properties': loadProperties();`
  (`app.js:1778`); `case 'insights': insTab(_insTab);` (`app.js:1787`, defaults to `_insTab='visual'`, `app.js:14291`).
- Permission model — `app.js:1310-1334`:
  - `NAV_PERMISSION['nav-link-properties'] = 'properties'` (`app.js:1312`) → toggled `d-none` in `applyRoleUI()` (`app.js:1350-1353`).
  - **`NAV_PERMISSION` has NO entry for `nav-link-insights`** → the nav link is never hidden by `applyRoleUI()`,
    regardless of permissions (a user without `crm` still sees the "هوش تصویری" link).
  - `SECTION_PERMISSION.properties = 'properties'`, `SECTION_PERMISSION.insights = 'crm'` (`app.js:1328-1329`) —
    insights is gated by the **CRM** permission, not a permission of its own.
  - `_isSectionAllowed()` (`app.js:1713-1718`) redirects to `_defaultSection()` if the perm is missing — so clicking
    "هوش تصویری" without `crm` perm silently bounces to another section (dashboard or first allowed section) even
    though the link was visible. **Bug/quirk to decide on purpose for Next.js**: either gate the nav link on `crm` too,
    or give insights its own permission key.
  - `_defaultSection()` (`app.js:1705-1711`).
- Backend enforcement: `app/api/routes/__init__.py:48` — `properties.router` mounted with
  `dependencies=_perm("properties")` (every `/properties/*` route requires the `properties` permission).
  `app/api/routes/__init__.py:60` — `crm.router` (includes `/crm/insights`, `/crm/match/*`) mounted with
  `dependencies=_perm("crm")`. So **"ملک‌های مشابه" / "متقاضیان هم‌خوان" buttons inside the properties detail modal
  call `/crm/match/...` and therefore require the `crm` permission**, even though the user is on the "properties"
  section with only the `properties` permission — a cross-permission dependency to replicate.
  Reference permission labels: `app/auth/permissions.py:38` (`"properties": "املاک"`), `:40` (`"crm": "..."`),
  default admin perms `:58` = `["properties", "crm", "stats"]`.
- `/scraper/cities` and `/scraper/categories` (used to populate this section's filters) are gated only by
  `get_staff_user` (any staff, not the `properties` permission) — `app/api/routes/__init__.py:53-56`.
- Global app init: `initApp()` only preloads `loadCities()` + `loadCategories()` `if (_hasPerm('properties'))`
  (`app.js:1396`).
- `apiCall()` / `_apiCallOnce()` (`app.js:1912-1980`): adds `Authorization: Bearer <token>`, JSON body/parse,
  401 → force logout, structured error `{code, detail, message}` surfaced via `showToast`. `phone_unverified`
  error code triggers a phone-verification popup and auto-retries the original call (`app.js:1913-1928`).

---

## 1. «لیست املاک» — Properties list section

HTML: `frontend/index.html:749-801` (section, filter bar, table, pagination).
Detail modal: `frontend/index.html:4123-4133` (`#propertyModal` / `#property-detail`).
Lightbox: `frontend/index.html:4924-4929` (`#img-lightbox`).
Match modal (reused): `frontend/index.html:4779-4789` (`#matchModal`).
JS: `app.js:2493-3043` (core), plus helpers scattered further down (`7845-7913`, `8204-8208`, `9869-9889`),
`frontend/js/ai/photo.js` (17-107), `frontend/js/ai/reader.js` (1-79), `frontend/js/ai/embed.js` (1-97, badge only).

### 1.1 Filter bar (`index.html:750-777`)
- Free-text search — `#search-properties` (`:754-755`) → `search` query param.
- City picker (custom searchable dropdown, not a plain `<select>`) — `#filter-city-picker` (`:756`) / hidden value
  `#filter-city-hidden` (`:757`). Built by `initCityPicker('filter-city-picker', cities, {valueId:'filter-city-hidden', useSlug:false, allLabel:'همه شهرها', allValue:'', placeholder:'جستجو...'})`
  — `app.js:3261-3267`, generic picker implementation `app.js:3044-3245`; data source `loadCities()` →
  `GET /scraper/cities` (`app.js:3247-3282`).
- Category `<select>` — `#filter-category` (`:758-760`), `onchange="onFilterCategoryChange()"`. Populated by
  `loadCategories()` → `GET /scraper/categories`, options built from the **same category list used by scraper and
  CRM filters**, `option.value = category.name`, `data-type = category.type` (buy/rent) — `app.js:3284-3308`.
  `_selectedCategoryType()` (`app.js:2496-2500`) reads `selectedOptions[0].dataset.type`.
- "جستجو" button → `loadProperties()` (`:761-763`).
- "خروجی اکسل" button → `exportPropertiesExcel()` (`:764-766`).
- "خروجی JSON" button → `exportProperties()` (`:767-769`).
- Rent-only band, shown/hidden via `onFilterCategoryChange()` toggling `.d-none` on `#rent-filters` when the
  selected category's type is `rent` (`app.js:2502-2505`; markup `index.html:771-776`):
  - `#filter-min-deposit`, `#filter-max-deposit` (ودیعه range)
  - `#filter-min-rent`, `#filter-max-rent` (اجارهٔ ماهانه range)

### 1.2 List load — `loadProperties()` (`app.js:2507-2592`)
- `GET /properties?page=&size=20` plus optional: `search`, `city`, `listing_type` (only sent when the selected
  category resolves to `buy`/`rent`), `category`, `min_deposit`, `max_deposit`, `min_rent_price`, `max_rent_price`.
  (`app.js:2517-2530`)
- Backend: `app/api/routes/properties.py:25-155` `get_properties()`. Filters supported by the endpoint but **not
  wired to any UI control**: `min_price`/`max_price`, `min_area`/`max_area`, `min_rooms`/`max_rooms`, `has_phone`,
  `owner_phone`, `sort_by`/`sort_order` (defaults `scraped_at desc`, no sort-UI at all) — worth deciding whether the
  Next.js rebuild adds sort/area/room/price controls that the API already supports.
  Query semantics: `city`/`category` are `ILIKE %term%`; `search` matches `title|description|district|neighborhood`
  via `ILIKE`; soft-deleted rows excluded (`Property.is_active == True`).
  Response = `PropertyListResponse{items:[PropertyResponse], total, page, size, pages}`.
- Empty state: "هیچ ملکی یافت نشد" + icon (`app.js:2536-2543`).
- Table columns (`index.html:781-789`, rows built `app.js:2547-2584`):
  1. کد ملک — `formatSerial(property.serial_no)` in a `.serial-badge` (grouping-free Persian digits, `app.js:1856-1859`).
  2. عنوان — title truncated to 40 chars with `…` only if actually cut (`app.js:2551-2555`), plus `agencyBadge(property)`
     inline (see §1.6).
  3. شهر — `city_name || '---'`.
  4. متراژ — `formatNumber(area) + ' متر'`.
  5. اتاق — `formatNumber(rooms)` or `---` (explicit null check, 0 is a valid value).
  6. قیمت — if `listing_type === 'rent'`: two lines "رهن: X" / "اجاره: Y" (`formatPrice`), else `formatPrice(total_price || price)`.
  7. شماره تماس — `tel:` link via `safeTel()` if present, else `noPhoneCell(property)` (§1.6).
  8. عملیات — 👁 `viewProperty(id)`, ↗ external link to `property.url` (`target=_blank`, via `safeUrl()`), 🗑 `deleteProperty(id)`.
- Pagination — `updatePagination(page, pages)` (`app.js:2594-2619`): «‹ 1 … 4 5 6 … 42 ›» windowed by ±2 around
  current page, `goToPage(page)` sets `currentPage` and re-calls `loadProperties()` (`app.js:2621-2624`).
- `formatNumber`/`formatSerial`/`formatPrice` — `app.js:1849-1859`, `1902-1910` (see §3 Formatting rules).

### 1.3 Property detail modal — `viewProperty(id)` (`app.js:2626-2938`)
- `GET /properties/{id}` → `PropertyResponse` (`app/api/routes/properties.py:213-227`).
  **⚠ Schema gap found**: `PropertyResponse` (`app/schemas/__init__.py:61-97`, built from `PropertyBase` `:17-41`)
  does **not** include several fields the modal template reads and renders as UI controls:
  `land_area`, `built_area`, `building_direction`, `frontage`, `unit_status`, `document_type`, `usage_type`,
  `building_age`, `extra_attrs`, `address`, `latitude`, `longitude`, `updated_at`. Since FastAPI's `response_model`
  strips unmodelled attributes, **these always arrive as `undefined` in the JSON today**, so those parts of the
  modal permanently render `---`/nothing even though the DB columns exist (`app/models/property.py:72-102,171,254`)
  and `PATCH` can write `building_direction`/`corner_type` back. Decide in the rebuild whether to fix the schema
  (add the fields) or intentionally drop these UI elements.
- Image carousel (`index.html`-style markup built inline, `app.js:2633-2658`): Bootstrap carousel `#propertyCarousel`
  built from `property.images[]`; each slide `onclick="openImageLightbox(this.src)"`; prev/next controls only when
  `images.length > 1`; footer caption "N تصویر"; if no images → `آلرت "بدون تصویر"`.
- AI photo tags box `#ai-photo-{id}` (`app.js:2660`) — filled by `aiLoadPhotoTags()` **only when**
  `['root','super_admin'].includes(_currentUser?.role)` (`app.js:2928-2930`) — role check is client-side; server
  additionally enforces `root`/`super_admin` via `_role_dep` (`app/api/routes/ai_photo.py:20-21,47-51`).
- Title row: `esc(title)` + `aiDuplicateBadge(property)` (guarded `typeof === 'function'`) (`app.js:2662`).
- Card «اطلاعات پایه»: کد ملک (+ `formatSerial`) & raw `tag_number` in `<code>`; شناسهٔ دیوار (`divar_id`);
  نوع آگهی (خرید/اجاره emoji labels); نوع ملک (`property_type`); دسته‌بندی (`category_name`); دارای تصویر
  (`has_images` ✅/❌) (`app.js:2664-2698`).
- Card «اطلاعات قیمت»: قیمت کل, قیمت هر متر, اجارهٔ ماهانه, ودیعه — each only rendered `if` truthy, via `formatPrice`
  (`app.js:2700-2733`).
- Card «مشخصات ملک»: متراژ, متراژ زمین (**broken, schema gap**), زیربنا (**broken**), تعداد اتاق, طبقه, کل طبقات,
  سال ساخت, سن بنا (**broken**), جهت ساختمان (**broken data**, inline `<select>` via `_propertyFieldSelect()` — see
  §1.4), نبش (inline `<select>`, works — `corner_type` IS in the schema), بر/متر (**broken**), وضعیت واحد (**broken**),
  نوع سند (**broken**), نوع کاربری (**broند**) (`app.js:2736-2800`).
- Extra attrs card («مشخصات تکمیلی») — only rendered `if Object.keys(property.extra_attrs||{}).length` — **dead
  today** since `extra_attrs` is missing from the response; labels come from `LEAD_ATTR_FA` (built from
  `LEAD_KIND_FIELDS`, `app.js:7083-7145`) (`app.js:2802-2817`).
- AI facts box `#ai-facts` (`app.js:2820`, `data-id`, `data-read-at=property.ai_read_at`) — filled by
  `aiRenderFacts()` unconditionally (any role sees it; only the "بازخوانی" re-read button is
  root/super_admin-gated) — see §1.5.
- Card «موقعیت مکانی»: شهر, منطقه (`district`), محله (`neighborhood`), آدرس (**broken — schema gap**), and — only
  if `latitude && longitude` (**never true today**, schema gap) — a "مشاهده در نقشه" button linking to
  `https://www.google.com/maps?q={lat},{lng}` (`app.js:2822-2859`). **Map link is effectively dead in production
  right now**; verify with backend before/while rebuilding.
- Card «توضیحات» — raw `description` in a `<pre>` (`white-space:pre-wrap`) or `---` (`app.js:2862-2872`).
- Card «اطلاعات تماس» — شماره تماس (tel: link or `noPhoneCell()`), فروشنده (`seller_name`) (`app.js:2874-2895`).
- Meta card — اسکرپ شده (`scraped_at`), آخرین بروزرسانی (`updated_at`, **broken — schema gap**), both
  `toLocaleString('fa-IR')` (`app.js:2897-2909`).
- Action row (`app.js:2911-2922`):
  - "مشاهده در دیوار" — external link to `property.url`.
  - "ملک‌های مشابه" — `showSimilarForProperty(id)` → opens `#matchModal` (§1.7).
  - "حذف" — `deleteProperty(id)` then closes the modal.
- Opens via Bootstrap `new bootstrap.Modal('#propertyModal').show()` (`app.js:2932-2933`).

### 1.4 Inline field editing (property modal only)
- `_propertyFieldSelect(propId, field, value, options, label)` (`app.js:7845-7853`) — renders a `<select>` whose
  `onchange` immediately calls `savePropertyField()`. Used for `building_direction` (options = `DIRECTION_OPTIONS`,
  `app.js:7072-7074`) and `corner_type` (options = `CORNER_OPTIONS`, `app.js:7079`, only `['دونبش','سه‌نبش']` —
  legacy values like «تک‌نبش»/«چهارنبش» stay selectable only if already stored, via the "keep out-of-list value" rule
  at `app.js:7847`).
- `savePropertyField(propId, field, value, el)` (`app.js:7855-7870`): `PATCH /properties/{id}` with
  `{[field]: value || null}`; disables the `<select>` while saving; on success updates `el.dataset.previous` and
  toasts "ذخیره شد"; on failure reverts the `<select>` to the previous value and toasts the API error.
  Backend: `app/api/routes/properties.py:264-287` `update_property()` — **note `PropertyUpdate` schema
  (`app/schemas/__init__.py:50-58`) only accepts `title`, `description`, `price`, `is_active`,
  `building_direction`, `corner_type`** — any other field name sent here is silently ignored by pydantic
  (`exclude_unset`), so inline-editing more fields later requires extending `PropertyUpdate` too.

### 1.5 AI "برداشت هوش مصنوعی" (listing-text reader) — `frontend/js/ai/reader.js`
- Renders inside `#ai-facts` via `aiRenderFacts(container, property.ai_facts)` (any role can view).
- Chips: نوع (`AI_PROPERTY_KIND_FA` map: apartment/house/land/shop/office/other), طبقه (+ "از N" total floors),
  ساخت (year_built), سند (document), وضعیت (condition), منطقه (district), amenities
  (`AI_AMENITIES`: has_elevator/has_parking/has_storage/has_balcony — shown as "دارد"/"بدون X"), deal flags
  (`AI_DEAL_FLAGS`: convertible/exchange/vacant/negotiable), «قیمت مقطوع» when `negotiable === false`,
  `suitable_for[]` chips, `red_flags[]` chips (⚠, warn style). Each chip's hover title shows confidence % from
  `facts.confidence[key]` (`reader.js:13-18,31-64`).
- Empty state: "هنوز خوانده نشده — خواننده هر دو دقیقه آگهی‌های تازه را می‌خواند." (background-loop cadence stated
  in copy, not polled by the frontend) (`reader.js:35`).
- Summary paragraph `facts.summary`, meta line `model · readAt (fa-IR locale)` (`reader.js:58-63`).
- "بازخوانی" button — root/super_admin only (`_currentUser?.role` check, `reader.js:20-26`) — `aiReread(id)`
  (`reader.js:66-78`): `POST /ai/reader/{id}` (`app/api/routes/ai_reader.py:36-47`, role-enforced server-side too),
  updates `data-read-at` to now and re-renders; spinner + disable on the button while in flight.
- Endpoints not surfaced in this UI at all but exist server-side (candidates for an admin "AI screen" elsewhere,
  not this section): `GET /ai/reader/status`, `POST /ai/reader/run` (`app/api/routes/ai_reader.py:22-33`).

### 1.6 Badges / special cells
- `agencyBadge(p)` (`app.js:7898-7913`) — shows «املاکی» badge when `p.agency_suspected`; red variant
  ("bg-danger") when the ad's own text says agency but Divar's own `advertiser_type`/`lead_advertiser_type` says
  "personal" (mismatch/"clash"); grey ("bg-secondary") otherwise; tooltip includes `agency_evidence` quote.
- `noPhoneCell(p)` (`app.js:7885-7896`) — three distinct blank-phone reasons, each rendered differently:
  `contact_channel === 'chat_only'` → "فقط چت" badge; `=== 'unavailable'` → "گرفته نشد" warning text; anything else
  → plain "---". **Important**: distinguishes "poster hid the number" vs "scrape failed, will retry" vs "predates
  the field" — must be preserved, not collapsed to a single "no phone" state.
- `aiDuplicateBadge(prop)` (`frontend/js/ai/embed.js:14-21`) — reads `prop.ai_duplicate_of` (an id); if a positive
  integer, renders «احتمالاً تکراری» badge with an "اصل" link that calls `viewProperty(id)` to jump to the original.
  Used on the property-modal title (`app.js:2662`) and on CRM lead rows/modal (out of scope here, but shares logic).

### 1.7 "ملک‌های مشابه" (similar properties) — reused match modal
- Trigger: property-detail modal action button → `showSimilarForProperty(id)` (`app.js:8204-8208`) →
  `_openMatchModal(title, '/crm/match/property/{id}?limit=12', emptyMsg)`.
- `_openMatchModal()` (`app.js:8181-8196`): opens `#matchModal` with a spinner, fetches the URL, renders via
  `_renderMatchModal()`; **stores the URL on `modalEl.dataset.matchUrl`** so a stale poll response for a
  since-closed/changed modal is discarded.
- Backend: `GET /crm/match/property/{property_id}?limit=&use_llm=true` (`app/api/routes/crm.py:1267-1284`,
  requires `crm` permission at router level, `get_current_user` at the route). Response:
  `{items: [...], total, source: _match_source(prop), reasons_pending: bool}`.
  `_match_source()` (`crm.py:1287-1296`): `{id, title, serial_no, district, city_name, listing_type, price,
  deposit (rent only), rent_price (rent only), comparable}`.
- Card rendering `_matchCard(m)` (`app.js:8083-8115`): score badge (color banded: ≥75 high / ≥50 mid / else low),
  title, serial badge, city/district/area/rooms line, up to 3 `reasons[]` tag chips, optional `ai_reason` line
  (stars icon), price (`_matchMoney`), optional `price_gap_pct`/`price_direction` badge (near ≤15% vs far), "جزئیات"
  button → `viewProperty(m.id)`, optional `tel:` link.
- **Polling for AI reasons**: `_pollMatchReasons(url, modalEl, triesLeft=3)` (`app.js:8168-8179`) — if
  `reasons_pending` is true, re-fetches the same URL every **4 seconds, up to 3 tries**, silently gives up on error,
  and re-renders in place without disturbing what's already shown; stops immediately if the modal was reassigned to
  a different URL meanwhile. **This is the one polling behavior in scope for these two sections.**
- Uses `_matchCriteria()` (`app.js:8122-8128`) to explain an empty result ("جستجو بر اساس: خرید • آپارتمان • تهران").

### 1.8 Delete — `deleteProperty(id)` (`app.js:3004-3014`)
- Confirmation via `askConfirm()` (custom dialog, not `window.confirm`) — icon `bi-trash3`, tone `danger`.
- `DELETE /properties/{id}` → **soft delete** (`app/api/routes/properties.py:290-308`: sets `is_active=False`,
  `updated_at=now`; row is never physically removed). On success: toast + `loadProperties()` refresh.

### 1.9 Exports
- **JSON export** — `exportProperties()` (`app.js:3016-3038`): `POST /properties/export` with
  `{city, listing_type}` (only `city` + the resolved buy/rent type — **category/search/deposit/rent filters are
  NOT forwarded**, unlike the Excel export). Backend `app/api/routes/properties.py:337-395`
  (`PropertyFilter` body model has more fields — `category`, `min/max price`, `min/max area`, `min/max rooms`,
  `has_phone`, `search` — **all unused by this button**). `format` query param defaults to `"json"`
  (`?format=csv` also implemented server-side but never invoked from the UI). Response `{data: [...], format}`;
  client builds a `Blob` and force-downloads `properties-export.json`. Row shape = `Property.to_dict()`
  (full column dump, not the `PropertyResponse` schema — richer than what `viewProperty` receives!).
- **Excel export** — `exportPropertiesExcel()` (`app.js:9869-9889`): `GET /properties/export/excel` with the
  **full current filter set** (`city`, `category`, `search`, `listing_type`, `min/max_deposit`, `min/max_rent_price`)
  via `_downloadExport()`. Backend `app/api/routes/properties.py:158-210`: capped at 10,000 rows, sorted by
  `serial_no desc`, columns (Persian headers, in order): کد ملک, عنوان, شهر, منطقه, دسته‌بندی, نوع آگهی, متراژ, اتاق,
  طبقه, سال ساخت, قیمت کل, قیمت هر متر, ودیعه, اجاره ماهانه, سند, پارکینگ (دارد/ندارد), آسانسور (دارد/ندارد), جهت,
  نبش, شماره تماس, فروشنده, لینک آگهی, تاریخ ثبت (`fa_date()` — Jalali-formatted, see `app/services/excel_export.py`).

### 1.10 Not wired to any properties-list UI (present server-side only)
- `GET /properties/tag/{tag_number}`, `GET /properties/divar/{divar_id}` (`properties.py:230-261`) — lookup by
  alternate keys, no button calls these from this section.
- `POST /properties/fix-has-images` (`properties.py:398-410`) — maintenance endpoint, no UI trigger found anywhere.
- `min_price`/`max_price`/`min_area`/`max_area`/`min_rooms`/`max_rooms`/`has_phone`/`owner_phone`/`sort_by`/
  `sort_order` query params on `GET /properties` (see §1.2) — accepted by the API, no filter/sort control in the UI.

---

## 2. «هوش تصویری» — Insights section (two tabs: تحلیل تصویری و قیمت / قیف و عملکرد)

HTML: `frontend/index.html:2518-2724`.
JS: `app.js:14085-14420` (+ shared formatters `14060-14084`), plus `frontend/js/ai/photo.js` for the status card.

### 2.1 Tab switcher
- `#ins-tabs` (`index.html:2520-2527`): «تحلیل تصویری و قیمت» (`id=ins-tab-visual`, icon `bi-eye`) and
  «قیف و عملکرد» (`id=ins-tab-pipeline`, icon `bi-funnel`).
- `insTab(which)` (`app.js:14293-14303`): toggles pane `display`/tab `.active`; loads `loadVisual()` for `visual`,
  `loadInsights()` for `pipeline`. Current tab kept in module state `_insTab` (used by `showSection()` re-entry).
- Default tab on section entry = whatever `_insTab` currently holds (persists across navigation within the same
  page load, not across reloads).

### 2.2 Tab A — «تحلیل تصویری و قیمت» (visual/valuation) — `loadVisual()` (`app.js:14305-14334`)
- `GET /properties/visual/overview?limit=25` (default `limit`; note this endpoint sits under the `properties`
  router/permission, **not** `crm`, even though it's shown on the "insights/crm-gated" section) —
  `app/api/routes/properties.py:413-541`.
- 4 stat cards (`index.html:2530-2555`): زیر قیمت منطقه (`vis-under` = `valuation.under.length`), ملک تکراری
  (`vis-dupes` = `duplicates.total_pairs`), آگهی با عکس ضعیف (`vis-weak` = `photos.weak.length`), قابل ارزش‌گذاری
  (`vis-judged` = `valuation.judged`). All formatted with `_insNum()` (renders `—` for null/undefined, never a fake 0).
- Coverage sentence `#vis-coverage` (`app.js:14325-14328`): "از {total} ملک، **{judged}** قابل ارزش‌گذاری بود —
  بقیه یا متراژ ندارند یا محله‌شان هنوز به {min_comparables} آگهی مشابه نرسیده. {districts_with_a_benchmark} محله
  از {districts_seen} محله پایهٔ قیمت دارد." — surfaces the exact business rule for "cannot judge" (no area, or
  district has fewer than `min_comparables` listings).

**«زیر قیمت منطقه» table** (`index.html:2559-2570`, render `_visUnder()` `app.js:14336-14354`):
- Columns: کد (serial_no), عنوان (title, truncated 260px), محله (district), متراژ (area), قیمت هر متر (`ppm`
  formatted `_insToman`), اختلاف (`delta_pct`% badge), پایه (`sample` count of comparables + "⚠" if
  `confidence === 'thin'`).
- Empty state: "هیچ ملکی به‌اندازهٔ قابل توجه زیر میانهٔ محله‌اش نیست."
- Backend computation: buckets listings by `val.bucket_key(p)` (district-level key), computes district median ppm
  via `val.benchmark()`, judges each listing with `val.assess()` → verdicts `under`/`over`/`unknown`; `under` list
  sorted ascending by `delta_pct` (cheapest-relative-to-median first); response only sends `under` (top `limit`) —
  **`over` is computed and returned by the API but has no UI table** (candidate to add in the rebuild, or confirm
  intentionally omitted).

**«یک ملک، چند آگهی» (duplicates) table** (`index.html:2572-2582`, render `_visDupes()` `app.js:14356-14388`):
- Note line: "{with_hashes} آگهی عکس اثرانگشت‌شده دارد · {boilerplate_ignored} عکس تکراری (لوگو/نما) نادیده گرفته شد".
- Columns: تشخیص (`note` badge — red `bg-danger-subtle` if `verdict==='duplicate'`, amber `bg-warning-subtle`
  otherwise/"maybe"), آگهی اول (serial + seller + price), آگهی دوم (same), اختلاف قیمت (`—` if either price is
  missing, "هر دو یک قیمت" if the gap is exactly 0, else the Toman-formatted gap — **0 and null are deliberately
  different renders**, per code comment `app.js:14370-14374`).
- Empty state: "ملک تکراری پیدا نشد. عکس‌ها از اسکرپ بعدی اثرانگشت می‌گیرند."
- Backend: pairwise comparison of all listings that have `image_hashes`, using `image_fingerprint.compare()` with
  `boilerplate()`-detected shared logo/watermark hashes ignored; pairs with `verdict === 'different'` dropped;
  sorted duplicate-verdict-first then by biggest price gap descending (`properties.py:469-499`).

**«کیفیت عکس‌ها» table** (`index.html:2584-2594`, render `_visPhotos()` `app.js:14392-14420`):
- Note line: per-problem counts using `PHOTO_PROBLEM_FA = {blurry:'تار', exposure:'نور نامناسب', small:'رزولوشن
  پایین'}`, or "{scored_listings} آگهی سنجیده شد" if no problems recorded.
- Columns: کد, عنوان (truncated 280px), تعداد عکس (`count`), بدترین (`worst` score), ایرادها (badges from
  `problems[]` via `PHOTO_PROBLEM_FA`, `—` if none).
- Empty state: "عکس ضعیفی پیدا نشد. کیفیت عکس‌ها از اسکرپ بعدی سنجیده می‌شود."
- Backend: only listings with `image_quality.scored === true` counted; "weak" = `worst < 50` OR has any recorded
  `problems`; sorted by `worst` ascending (worst photos first), `999` sentinel for missing worst.

**«برچسب‌های هوش تصویری» card** (`index.html:2597-2600`, `#ai-photo-status`):
- Filled by `aiLoadPhotoStatus()` (`frontend/js/ai/photo.js:85-92`) → `GET /ai/photo/status`
  (root/super_admin only server-side, `app/api/routes/ai_photo.py:19-21,30-32`) → `aiRenderPhotoStatus()`
  (`photo.js:68-83`): 3 mini-stats (برچسب‌خورده = `tagged`, بدون عکس = `skipped`, در صف = `behind`), status line
  ("هوش مصنوعی تنظیم نشده" / "از پنل خاموش است" / "فعال — هر ۵ دقیقه یک دور" — background-loop cadence stated in
  copy) + model name + prompt version, and a "یک دور الان" button (disabled unless `configured && enabled`).
- Button → `aiRunPhotoPass(btn)` (`photo.js:94-107`): `POST /ai/photo/run?limit=30` (root/super_admin only), toasts
  the pass result (`tagged`/`skipped`/`failed` counts; if `stopped` — reason mapped via
  `AI_PHOTO_STOP_FA = {BudgetExceeded, Disabled, NotConfigured}` — shown as a warning toast), then reloads the
  status card. **Called from `loadVisual()` unconditionally regardless of role** (`app.js:14333`) — `aiLoadPhotoStatus`
  itself just renders an empty box on a 403 for non-admins (no visible error).
- (Note: `GET/POST /ai/photo/{property_id}` — single-listing tag fetch/retag — is **not** used on this page; it's
  only invoked from the properties-list detail modal, §1.3.)

**Static disclaimer card** (`index.html:2602-2610`) — "این صفحه چه چیزی را نمی‌سنجد": explicitly states the page
does NOT do kitchen-style/flooring/material/brand detection, floor-plan generation, or image editing (virtual
staging/sunset); "برچسب‌های هوش تصویری" above comes from a vision model over the first three photos only; everything
else on the page is measured from real pixels/prices, not estimated. **Preserve this disclaimer verbatim** — it's a
deliberate trust/expectations statement, not filler copy.

### 2.3 Tab B — «قیف و عملکرد» (pipeline) — `loadInsights()` (`app.js:14085-14119`)
- `GET /crm/insights?days={14|30|90}` (requires `crm` permission at router level) —
  `app/api/routes/crm.py:2995-3094`, aggregation helpers in `app/services/crm_insights.py`.
- Window selector `#ins-window` (`index.html:2667-2672`): 14 / 30 (default, `selected`) / 90 days,
  `onchange="loadInsights()"`.
- 4 headline stats (`index.html:2617-2642`): کل لیدها (`totals.leads`, `_insNum`), نرخ تبدیل به معامله
  (`totals.conversion_rate`, `_insPct` — **`None`/`—` when `total_leads === 0`, never a fake `0%`**,
  `crm_insights.py:67-76`), لید بی‌پیگیری (`stalled.items.length`), کمیسیون وصول‌نشده
  (`deals.commission_due`, `_insToman`).
- Coverage sentence `#ins-coverage` (`app.js:14112-14118`): "{leads_with_phone}% لیدها شمارهٔ تماس دارند ·
  {properties_with_phone}% املاک شمارهٔ تماس دارند" — same "never fake a number" rule (`coverage()`,
  `crm_insights.py:231-240`).

**قیف فروش (funnel)** — `#ins-funnel`, `_insRenderFunnel()` (`app.js:14121-14146`):
- Ordered stages from `crm_insights.funnel()` (`crm_insights.py:26-64`): جدید → تماس گرفته‌شده → واجد شرایط →
  در حال مذاکره → موفق → از دست رفته, **plus any unrecognised `Lead.status` value appended at the end**, flagged
  `unexpected: true`, rendered with a warning color + ⚠ icon and tooltip "وضعیت ناشناخته" — a genuine data-quality
  signal, must not be dropped in the rebuild. Bars are relative-width to the max stage count; note shows the total.
  Palette `INS_PALETTE` (`app.js:14068-14069`, 10 fixed hex colors, cycled by index).

**دمای مشتریان (customer temperature)** — Chart.js doughnut `#ins-temp-chart`, `_insRenderTemp()`
(`app.js:14148-14170`): buckets `hot/warm/cold` (+ any other) mapped to Persian (`داغ/گرم/سرد`), fixed color set
`['#fb7185','#fcd34d','#67e8f9','#64748b']`, `cutout:'62%'`, legend bottom.

**روند لید و ملک (trend)** — Chart.js line `#ins-trend-chart`, `_insRenderTrend()` (`app.js:14172-14206`):
two series (لید, ملک) over the selected day window, **gap-filled per day including zero-count days**
(`crm_insights.daily_series()` — deliberately not sparse, so a quiet week doesn't look artificially smooth),
x-axis labels via `toLocaleDateString('fa-IR', {day:'numeric', month:'long'})` (Jalali via Intl, no external lib).

**پراکندگی شهرها (cities)** — Chart.js horizontal bar `#ins-city-chart`, `_insRenderCities()`
(`app.js:14208-14234`): top buckets from `crm_insights.top_buckets()` (`crm_insights.py:143-162` — top 6 + "سایر"
overflow bucket flagged `is_other`, grey `#64748b`), rest colored from `INS_PALETTE`.

**عملکرد مشاوران (agent scoreboard)** — table `#ins-agents`, `_insRenderAgents()` (`app.js:14236-14254`):
columns مشاور (+ "N روز ثبت‌شده" subtext), فایل نو (`new_files`), بازدید (`showings`), پیشنهاد (`offers`), معامله
(`closed`, bold), بازدید/معامله (`showings_per_close`, `—` if `closed === 0` to avoid a divide-by-zero-looking
value). Sourced from `DailyPerformance` rows summed per agent, sorted by closed desc, then offers desc, then
showings desc (`crm_insights.agent_scoreboard()`, `crm_insights.py:165-193`). Empty state: "هنوز عملکرد روزانه‌ای
ثبت نشده است."

**لیدهایی که معطل مانده‌اند (stalled leads)** — table `#ins-stalled-list`, `_insRenderStalled()`
(`app.js:14256-14274`): note "بیش از {after_days} روز" (`STALE_AFTER_DAYS = 7`, `crm_insights.py:42`). Columns:
نام (`seller_name || phone_number || '—'`), شهر, وضعیت (status_label badge), بی‌حرکت (idle_days). Only leads in
`OPEN_STAGES = (new, contacted, qualified, negotiating)` whose `updated_at` (fallback `created_at`) is older than
the cutoff qualify; sorted most-idle first, capped at 50 rows server-side (`crm.py:3019-3029`,
`crm_insights.stalled_leads()` `crm_insights.py:79-113`). Empty state: "هیچ لید معطلی نیست."

**Bottom line** `#ins-coverage` in the pipeline pane too (`index.html:2722`) — same coverage sentence rendered once
per tab-load (reused element id, see §2.3 head).

### 2.4 Formatting rules specific to this section (must be replicated exactly)
- `_insNum(v)` — `—` for null/undefined, else `Number(v).toLocaleString('fa-IR')` (`app.js:14071-14073`).
- `_insPct(v)` — same null rule, appends `٪` (`app.js:14074-14076`).
- `_insToman(v)` — null → `—`; ≥1e9 → "X میلیارد" (1 decimal); ≥1e6 → "X میلیون" (0 decimals); else plain
  localized number (no "تومان" suffix, unlike `formatPrice`) (`app.js:14077-14083`).
- General rule stated in the source comment (`app.js:14061-14064`, `crm_insights.py:10-13`): **a value the server
  cannot compute must be `null`, rendered `—` — never a synthetic `0`**, because a real zero and "unknown" must be
  visually distinguishable. This applies to every stat above (conversion rate, coverage %, showings_per_close, the
  valuation `under`/`judged` counts, etc.) — a critical behavior to keep in the Next.js version, not just a
  cosmetic default.

---

## 3. Shared formatting / infra used by both sections

- `formatNumber(num)` (`app.js:1849-1852`) — Persian digit grouping via `Intl.NumberFormat('fa-IR')`; `null`/
  `undefined`/`NaN` → `---` (note: different placeholder than `_insNum`'s `—` — two different dash conventions
  coexist across the two sections; decide whether to unify).
- `formatSerial(num)` (`app.js:1856-1859`) — same locale but `useGrouping:false` (IDs must never show thousands
  separators, and must visually match what a user types into search) → `—` on null.
- `formatPrice(price)` (`app.js:1902-1910`) — `0`, `null`, `undefined`, `NaN` all → `---`; ≥1e9 → "X میلیارد"
  (1 decimal, rounded to nearest 100M to avoid e.g. 3.5 rounding to 4); ≥1e6 → "X میلیون" (1 decimal); else
  "{number} تومان".
- `esc()` (`app.js:1873-1877`) — escapes `& < > " ' \``, used everywhere strings are interpolated into `innerHTML`.
- `safeUrl()` (`app.js:1825-...`) — only allows `http(s)` and relative paths; anything else (incl. `javascript:`)
  becomes `#`, used for the "مشاهده در دیوار" links and image `src`s.
- `safeTel()` (`app.js:1844-1846`) — strips everything except digits and `+` before building `tel:` links.
- Jalali dates: this section relies entirely on `Date.prototype.toLocaleDateString('fa-IR', ...)` /
  `toLocaleString('fa-IR')` (native Intl Persian calendar) for display — e.g. scraped/updated timestamps
  (`app.js:2902,2905`), trend-chart x-axis labels (`app.js:14180`). The heavier Jalali↔Gregorian conversion helpers
  (`jalaliToGregorian`, `gregorianToJalali`, `jalaliToDate`, `app.js:7451-7485,10287-10400`) are used elsewhere
  (scraper date-mode, CRM date filters) but **not** inside properties-list or insights — confirm the Next.js
  rebuild doesn't need a Jalali picker here, only Jalali-formatted display strings.
- `apiCall()`/`_apiCallOnce()` (`app.js:1912-1980`) — central fetch wrapper (see §0).
- Toasts: `showToast(title, message, type)` (`app.js:1805-1817`) — Bootstrap toast, used for every success/error in
  both sections.
- `askConfirm()` — custom confirm dialog used by `deleteProperty()` (not the browser's native `confirm()`).

---

## 4. Cross-references intentionally excluded (confirmed out of scope for these two sections)

- `_renderPropertyDetails(p)` (`app.js:7917-...`) — a *second*, richer property-detail renderer (includes
  has_elevator/has_parking/has_storage/has_balcony امکانات rows) used **only** inside the CRM lead-detail modal
  (`viewLead()`, called at `app.js:8283`), **not** by `viewProperty()`. The two "identical" detail views
  (per a stale code comment at `app.js:7915-7916`) have actually diverged — flag this for product decision when
  unifying in Next.js (should the properties-list detail modal gain amenities rows too?).
- `shareFile()` / `showCustomersForProperty()` / `aiOpenSemanticSearch()` (جستجوی معنایی) — invoked only from the
  CRM/«کمد و زونکن» (filing) cards and the CRM AI search box (`index.html:2149,2151`), not from the properties list
  or insights section. `aiDuplicateBadge()` is shared/reused (see §1.6) but the semantic-search entry point itself
  lives in CRM.
- `GET/POST /ai/embed/status`, `/ai/embed/run`, `/ai/embed/search`, `/ai/embed/similar/{id}`,
  `/ai/embed/duplicates/{id}` (`app/api/routes/ai_embed.py`) — none of these are called from properties-list or
  insights; `/ai/embed/search` backs the CRM "جستجوی معنایی" box only. Note for completeness since they live in the
  same AI family as the photo/reader features that *are* used here.
- `moveFilePick()` (بایگانی در زونکن) — only in the CRM lead-detail modal, not the properties list.
