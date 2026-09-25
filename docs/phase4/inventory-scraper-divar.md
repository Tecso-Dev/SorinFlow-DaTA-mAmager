# فهرست مهاجرت به Next.js — اسکرپر / احراز هویت دیوار / پراکسی‌ها / فرستندهٔ پیامک

منبع قدیم: `frontend/index.html` + `frontend/js/app.js`.
منبع جدید (اسکلت موجود): `frontend-next/src/components/panel/nav.ts` (هر چهار مسیر با `step: 6`، هنوز placeholder در
`frontend-next/src/app/panel/(app)/[...section]/page.tsx`)، کلاینت API در `frontend-next/src/lib/api.ts`
(کوکی httpOnly + هدر `X-CSRF-Token`؛ **نه** `localStorage.sf_token` مثل پنل قدیم)، و گِیت شمارهٔ موبایل در
`frontend-next/src/components/panel/phone-gate.tsx` که `PhoneGateDetail` (`code: "phone_unverified"`) را می‌گیرد —
دقیقاً همان چیزی که `require_verified_phone` در بک‌اند می‌فرستد.

بک‌اند: `app/api/routes/scraper.py` (۱۶۸۴ خط)، `app/api/routes/auth.py` (۱۰۱۱ خط)، `app/api/routes/proxies.py` (۲۵۹ خط)،
`app/api/routes/forwarder.py` (۳۷۵ خط)، `app/api/routes/monitoring.py` (بخش «وضعیت نشست‌های دیوار»)،
`app/api/routes/sms.py` (لاگ فرستنده)، `app/api/routes/stats.py` (لاگ اسکرپر)، `app/auth/permissions.py`،
`app/api/routes/__init__.py` (سیم‌کشی permission → router).

قاعده‌های محصول (از CLAUDE.md — باید در پیاده‌سازی تازه هم برقرار بمانند):
1. هر چیزِ یک حساب دیوار (شماره، کد پیامکی، پنجرهٔ احراز هویت، اعلان) فقط برای **صاحب همان شماره** است.
   root مدیریت می‌کند ولی کد کسی را دریافت یا جواب نمی‌دهد.
2. مالکیت شمارهٔ دیگران فقط در یک جا عوض می‌شود: کارت «همهٔ شماره‌های دیوار و صاحبشان» در بخش احراز هویت دیوار،
   فقط برای root، با ثبت در audit — `PATCH /api/auth/registry/{id}/owner`. ورود به دیوار، وارد کردن کوکی و
   فورواردر مالک شماره‌ای را که صاحب دارد **عوض نمی‌کنند**. شمارهٔ بی‌صاحب مال کسی می‌شود که واردش کند.
   مسیر انتقال دیگری اضافه نشود.
3. پنل: RTL و تیره، فقط با design tokenهای خود سایت. `prompt`/`confirm`/`alert` مرورگر ممنوع — معادل React باید
   دیالوگ‌های خود پنل باشد (در پنل قدیم: `askConfirm`/`askText`/`askInfo` در app.js:6303-6405؛ در پنل تازه باید
   با `@/components/ui/alert-dialog` یا معادل ساخته شود، نه `window.confirm`).
   از ظاهر پنجرهٔ OTP فعلی الهام گرفته شود.
4. `/api/auth/login`, `/api/auth/verify`, `/api/auth/refresh` روی Ingress **صراحتاً** به سرویس `worker` می‌روند
   (`k8s/base/ingress.yaml:65-85`, `pathType: Exact`) نه `backend` — چون این مسیرها Chromium/کوکی زندهٔ دیوار
   لازم دارند که فقط پاد `worker` دارد. بقیهٔ `/api/auth/*` (cookies, registry, ...) و همهٔ `/api/scraper`,
   `/api/proxies`, `/api/forwarder` هم روی همان مسیر عادی `backend` باقی می‌مانند (فقط همین سه مسیر Exact
   استثنا هستند). این باید در تنظیمات پروکسی/rewrite پنل Next.js هم رعایت شود (یا از همان Ingress عبور کند).
5. هیچ SSE/WebSocket در کل این چهار بخش نیست — همه‌چیز polling با `setInterval` روی HTTP است (بررسی‌شده:
   `EventSource`/`WebSocket` در app.js صفر مورد).
6. کانتینر tzdata ندارد؛ زمان‌های تهران با آفست ثابت `+03:30` محاسبه می‌شوند، نه `ZoneInfo`.

---

## ۰) زیرساخت مشترک این چهار بخش

- **دسترسی/نقش:**
  - permission keyها (`app/auth/permissions.py:37-53`): `scraper`, `divar_auth` (بخش احراز هویت دیوار)،
    `forwarder`, `proxies`. سیم‌کشی روتر ↔ permission در `app/api/routes/__init__.py:48-68`.
  - `root` و `super_admin` از همهٔ چک‌های permission عبور می‌کنند (`FULL_ACCESS_ROLES`). `admin` فقط اگر کلید در
    `users.permissions` تیک خورده باشد. `visitor` اصلاً به هیچ‌کدام از این چهار روتر نمی‌رسد (در `require_staff`
    رد می‌شود).
  - چند اکشن حساس علاوه‌بر permission به **شمارهٔ موبایل تأییدشدهٔ خودِ کاربر پنل** هم نیاز دارند
    (`Depends(require_verified_phone)` در بک‌اند؛ خطای ۴۰۳ با `detail.code == "phone_unverified"`):
    `POST /auth/login`, `POST /auth/cookies/import`, `POST /scraper/start`, `POST /scraper/jobs/{id}/resume`,
    `POST /scraper/jobs/{id}/switch-account`, `POST /scraper/schedules` (ساخت)، `POST /scraper/schedules/{id}/run`،
    `POST /scraper/scrape-single`, `POST /scraper/rescrape`, `POST /forwarder/devices` (ساخت گوشی).
    در پنل تازه این با `frontend-next/src/lib/api.ts`'s `setPhoneGate` / `frontend-next/src/components/panel/phone-gate.tsx`
    هندل می‌شود — باید همین مکانیزم را برای هر ۹ اکشن بالا صدا بزنند، نه یک alert ساده.
  - سطرهای دادهٔ حساس (کوکی/سشن دیوار، دستگاه فورواردر) با «مالکیت» فیلتر می‌شوند نه با نقش:
    `_usable_by`/`_own_sessions_only` در `auth.py:508-536`. `mine=1` روی `/auth/cookies` یعنی «چیزی که این کاربر
    می‌تواند **استفاده** کند»؛ بدون آن یعنی «چیزی که این کاربر می‌تواند **ببیند**» (root/super_admin همه را
    می‌بینند اما فقط مال خودشان را استفاده می‌کنند).
- **دیالوگ‌های عمومی (باید در کیت UI تازه معادل داشته باشند):** `askConfirm`/`askText`/`askInfo` — `app.js:6303-6405`.
  هیچ کدام از چهار بخش از `window.confirm/prompt/alert` استفاده نمی‌کند و نباید بکند.
- **Toast:** `showToast(title, message, type)` — `app.js:1805-1817`، روی Bootstrap Toast. معادل React (مثلاً
  `frontend-next/src/components/toaster.tsx`، از قبل موجود برای CRM) باید استفاده شود.
- **راهبری بخش/lifecycle polling:** `showSection()` در `app.js:1737-1802`. نکتهٔ مهم برای پورت به React:
  - ورود به «اسکرپر» → `loadJobs(); loadSchedules(); loadScraperAccounts(); _wireEstimateRefresh(); scheduleEstimate();
    checkDivarSessionBanner(); startOtpPolling(); startJobPolling(); checkPhoneGate(); _initScraperDatePicker();
    refreshDivarSessionCount(); restoreScraperForm();` (خط ۱۷۷۹-۱۷۸۱)
  - ورود به «احراز هویت دیوار» → `checkAuthStatus(); loadCookies(); loadNumbersRegistry(); checkPhoneGate();` (۱۷۸۲)
  - ورود به «فرستندهٔ پیامک» → `loadForwarders(); loadForwarderLog(); checkPhoneGate();` (۱۷۸۳)
  - ورود به «پراکسی‌ها» → `loadProxies();` (۱۷۸۵)
  - خروج از هر بخش → `stopOtpPolling(); stopJobPolling();` (۱۷۴۷-۱۷۴۸) — یعنی پولینگ فقط وقتی صفحه باز است اجرا
    می‌شود. در Next.js معادلش `useEffect` با cleanup روی mount/unmount کامپوننت صفحه است، نه یک تایمر global.

---

## ۱) «اسکرپر» (nav key: `scraper`, permission: `scraper`)

index.html: بخش اصلی `804`–`1111` (`<div id="section-scraper">`)؛ مودال‌های وابسته: لاگ اسکرپر `5108`–`5144`،
پنجرهٔ OTP دیوار `5146`–`5200`، گزارش تسک `4096`–`4106`، آگهی‌های ردشده `4108`–`4121`، هشدار «بدون ورود» `5063`–`5092`.

### ۱.۱ فرم «اسکرپینگ جدید» (`806`–`1042`)
- **از روی لینک دیوار** (`818`–۸۳۰) — اینپوت `#scraper-link` + دکمهٔ «پر کن».
  JS: `applyDivarLink()` — `app.js:3756`–`3815`.
  Endpoint: `POST /api/scraper/parse-link`
  body: `{ "url": string }`
  response: `{ city, city_name, category, category_name, filters: {...قیمت/ودیعه/متراژ/اتاق/advertiser_type/has_images}, ignored: string[] }`
  رفتار: **فقط پر می‌کند، خودش اسکرپ را شروع نمی‌کند** (عمداً read-only، طبق کامنت کد).
  فیلدهایی که پاک/پر می‌شوند از `_LINK_FIELD_IDS` می‌آیند (باید در کد جدید پیدا و پورت شود).
- **شهر** (`832`–۸۳۶) — city-picker سفارشی (`#scraper-city-picker` → hidden `#scraper-city`).
  داده از `GET /api/scraper/cities` (`lookup_router`, نیاز فقط به staff — permission ندارد چون properties/CRM هم
  از همین‌ها استفاده می‌کنند؛ `scraper.py:1056-1062`) → `[{slug, name, province}]`.
- **دسته‌بندی** (`837`–۸۴۰) — `<select id="scraper-category">` + `onScraperCategoryChange()` (`app.js:3310`) که
  فیلترهای خرید/اجاره را toggle می‌کند.
  داده از `GET /api/scraper/categories` (همان `lookup_router`) → `[{slug, name, type}]`.
- **شمارهٔ دیوار** (`844`–۸۵۴) — سلکت «خودکار — کم‌مصرف‌ترین» + `#scraper-account-note` + لیست سوییچ‌دار
  `#scraper-account-list` (روشن/خاموش هر شماره).
  JS: `loadScraperAccounts()` (`3581`–۳۶۰۹)، `_renderScraperAccountList()` (`3624`–۳۶۵۱)،
  `toggleDivarNumber(id, enabled)` (`3653`–۳۶۷۱)، `onScraperAccountChange()` (`3740`–۳۷۵۴).
  Endpoint لیست: `GET /api/auth/cookies?mine=1` → `{ sees_every_session, cookies: [{id, phone_number, is_valid,
  expires_at, last_checked_at, created_at, reveals, challenged_at, last_used_at, owner_user_id,
  identity_required_at, is_enabled, owner_name}] }`.
  Endpoint روشن/خاموش: `PATCH /api/auth/cookies/{cookie_id}` body `{ "enabled": bool }` →
  `{ success, id, phone_number, is_enabled, moved_jobs: string[] }` (اگر خاموش شود و اسکرپی روی همان شماره در
  حال اجرا باشد، آن جاب به شمارهٔ دیگر صاحبش منتقل می‌شود — `moved_jobs`).
  **قاعدهٔ مالکیت:** این لیست با `mine=1` است یعنی فقط شماره‌های **خودِ** کاربر — حتی root فقط مال خودش را در
  این سلکتور می‌بیند (نه کل pool). این را عمداً نگه دارید؛ نسخهٔ قبلی این باگ را داشت.
- **فیلترهای بیشتر** (`862`–۹۸۷, `<details id="scraper-more">`) — قیمت خرید (کل/هر متر)، ودیعه/اجارهٔ ماهانه،
  متراژ، تعداد اتاق، نوع آگهی‌دهنده (شخصی/مشاور املاک)، ویژگی‌ها (عکس/آسانسور/پارکینگ/انباری/بالکن)،
  تاریخ انتشار شمسی (date-picker، `_initScraperDatePicker()` ۳۳۲۲–۳۳۳۸, `_onScraperDateChange()` ۳۳۳۹–۳۳۶۰,
  `clearScraperDate()` ۳۳۶۱). این بلوک به‌طور خودکار باز می‌شود اگر فیلتری فعال باشد
  (`_scraperActiveFilters()` ۳۸۶۸–۳۸۷۸, `_scraperMoreSync()` ۳۸۸۰+) — این رفتار حتماً پورت شود؛ در موبایل حیاتی
  بود («فیلتر هیچ‌وقت نامرئی اعمال‌شده نماند»).
- **تعداد آگهی برای اسکرپ** (`988`–۹۹۴) — `#scraper-pages` (خالی = همهٔ روز، فقط وقتی تاریخ انتخاب شده).
- **چرخش شماره دیوار** (`995`–۱۰۰۹) — `#scraper-rotate-every` + `renderRotationHint()`.
  ⚠️ عدد کمتر = چرخش بیشتر = پیامک احراز هویت کمتر؛ ۰ = بدون چرخش. این توضیح باید عیناً در UI تازه بماند
  (کامنت کد صریحاً می‌گوید جهتش خلاف انتظار طبیعی است).
- **دانلود تصاویر** چک‌باکس (`1010`–۱۰۱۳).
- **«چند آگهی با این فیلترها هست؟»** (`1014`–۱۰۱۸) — `estimateScrape()` (`3923`–۳۹۸۰)، با debounce خودکار روی
  تغییر فرم (`scheduleEstimate()` ۳۹۰۴، `_wireEstimateRefresh()` ۳۹۱۲، ۹۰۰ms).
  Endpoint: `GET /api/scraper/estimate?city=&category=&advertiser_type=&has_images=&min_price=&max_price=&
  min_deposit=&max_deposit=&min_rent=&max_rent=&min_area=&max_area=&min_rooms=&max_rooms=&has_elevator=&
  has_parking=&has_storage=&min_price_per_meter=&max_price_per_meter=` (همه Optional جز `city`)
  response: `{ count: int|null, error: string|null, applied_by_divar: string[], applied_after_scrape: string[] }`
  (`applied_after_scrape` = فیلترهایی که دیوار نمی‌فهمد و فقط اسکرپر بعد از باز کردن هر آگهی اعمال می‌کند —
  یعنی نتیجهٔ واقعی مساوی یا کمتر از این عدد است؛ این هشدار باید در UI بماند).
- **دکمهٔ «شروع اسکرپینگ»** — `startScraping(e)` (`3982`–۴۰۴۲) → `executeBulkScraping()` (`4176`–۴۲۰۹).
  اگر کوکی معتبر نباشد، اول مودال هشدار «بدون ورود» (`cookieWarningModal`, `5063`–۵۰۹۲) باز می‌شود
  (`showCookieWarning()` ۵۲۸۲–۵۲۹۸, `continueScraping()` ۵۳۰۰–۵۳۱۱, `goToAuthSection()` ۵۳۱۳–۵۳۱۷).
  Endpoint: `POST /api/scraper/start` (نیاز به `require_verified_phone`)
  body (`ScrapingJobCreate`, `app/schemas/*.py:127-169`):
  ```
  city, category (str, required unless urls[])
  urls?: string[]           // اسکرپ لیست دستی — city/category فقط برچسب می‌شوند
  max_items?: int           // خالی در حالت تاریخ = کل روز
  download_images: bool = true
  divar_phone?: string      // باید مال خودِ کاربر باشد وگرنه 403
  min_price/max_price, min_deposit/max_deposit, min_rent/max_rent,
  min_price_per_meter/max_price_per_meter, min_area/max_area, min_rooms/max_rooms: int?
  has_images/has_elevator/has_parking/has_storage/has_balcony: bool?
  advertiser_type?: "personal"|"agency"
  max_age_hours?: int
  posted_date?: "YYYY-MM-DD" (Gregorian; اگر ست شود max_items/max_age_hours نادیده گرفته می‌شوند)
  rotate_every?: int
  ```
  response (`ScrapingJobResponse`): `{ id, job_id, divar_phone, status: "pending", created_at, ... (بقیه هنگام start خالی) }`
  خطاها: ۴۰۰ شهر/دسته‌بندی نامعتبر، ۴۰۳ شمارهٔ متعلق به کاربر دیگر، ۴۰۹ شمارهٔ ذخیره‌شده خاموش است (فقط حالت
  interactive)، ۴۲۹ بیش از ۳ جاب هم‌زمان در حال اجرا.
- **«هر روز خودکار اجرا شود»** — `saveAsSchedule()` (`4076`–۴۰۹۸)؛ دو پرامپت متنی سفارشی (ساعت HH:MM، اسم).
  Endpoint: `POST /api/scraper/schedules` (نیاز `require_verified_phone`)
  body: `{ name: str(1-120), config: dict(همان شکل ScrapingJobCreate ولی از `_scrapeFormConfig()` app.js:4046-4073
  ساخته می‌شود، بدون شمارهٔ انتخابی اگر خودکار باشد), hour: 0-23, minute: 0-59, enabled: bool=true }`
  response: شیء schedule (`_schedule_view`) — `{id, name, config, hour, minute, enabled, next_run_at,
  last_run_at, last_result, owner_name, city_name, category_name}`.
- **اسکرپ تکی** (`1029`–۱۰۴۲) — `#single-url` + دکمه → `scrapeSingle()` (`5234`–۵۲۵۰) →
  `executeSingleScraping()` (`5252`–۵۲۷۹).
  Endpoint: `POST /api/scraper/scrape-single` (نیاز `require_verified_phone`)
  body: `{ "url": string }` (باید شامل `divar.ir/v/` باشد — چک سمت کلاینت هم `frontend` هم `backend`)
  response: `ScrapingJobResponse` مثل `/start` — **یک جاب واقعی با همان جدول/لاگ/OTP** می‌سازد، دیگر
  synchronous نیست (این رفتار عمداً عوض شده — قبلاً inline بود، حالا مثل هر جاب دیگر در جدول ظاهر می‌شود).

### ۱.۲ زمان‌بندی‌های ذخیره‌شده (`1046`–۱۰۶۹, `#schedules-card`, وقتی حداقل یک ردیف باشد نمایش داده می‌شود)
جدول: نام (+نام مالک اگر بیننده «همه» باشد)، چه چیزی (شهر/دسته/max_items/max_age_hours)، ساعت (+next_run_at)،
آخرین اجرا (وضعیت+زمان)، روشن (سوییچ)، عملیات (اجرای فوری/تغییر ساعت/حذف).
JS: `loadSchedules()` (`4103`–۴۱۳۵), `toggleSchedule()` (`4137`–۴۱۴۳), `editScheduleTime()` (`4145`–۴۱۵۷),
`runScheduleNow()` (`4159`–۴۱۶۶), `deleteSchedule()` (`4168`–۴۱۷۴).
- `GET /api/scraper/schedules` → `{ schedules: [...], can_see_all: bool }` (root/super_admin همه را می‌بینند و
  می‌توانند خاموش/روشن/حذف کنند، بقیه فقط مال خودشان).
- `PATCH /api/scraper/schedules/{id}` body: `{name?, hour?, minute?, enabled?}` (Partial).
- `DELETE /api/scraper/schedules/{id}`.
- `POST /api/scraper/schedules/{id}/run` (نیاز `require_verified_phone`) → همان مسیری که کلاک ساعت ۸ می‌رود؛
  همیشه **به‌عنوان صاحب schedule** اجرا می‌شود، نه کسی که دکمه را زد.
  response: `{ ...نتیجهٔ fire(), schedule: {...} }`.
⚠️ note همیشگی زیر جدول: «هر زمان‌بندی با حساب‌های دیوار **صاحبش** اجرا می‌شود» و «اگر حداکثر سن آگهی خالی
باشد فقط ۲۴ ساعت اخیر گرفته می‌شود» — باید در UI تازه هم بماند (`1064`–۱۰۶۷).

### ۱.۳ جدول «تسک‌های اسکرپینگ» (`1070`–۱۱۰۸)
هدر: `#divar-session-badge` (نشست دیوار فعال/غیرفعال — `checkDivarSessionBanner()` ۵۴۲۴–۵۴۴۳)، فیلتر دسته‌بندی
(`#jobs-filter-category`)، دکمهٔ «لاگ اسکرپر» (`openScraperLog()` ۵۱۵۶–۵۱۵۹)، بروزرسانی.
ستون‌ها: `#`, دسته‌بندی، شهر، کاربر/حساب (+حساب‌های استفاده‌شده در tooltip اگر بیش از ۱)، وضعیت (+`finish_reason`
+ نشانهٔ «ادامهٔ …»)، پیشرفت (progress bar)، بررسی/کل، جدید/بروز، شروع، عملیات.
- `GET /api/scraper/jobs?status=&category=&limit=20` → `ScrapingJobList` (`items: ScrapingJobResponse[], total`).
  فیلدهای پاسخ کامل (`app/schemas/*.py:171-204`): `id, job_id, city_id, category_id, city_name, category_name,
  divar_phone, accounts_used[], owner_user_id, owner_name, status, total_pages, scraped_pages, total_items,
  scraped_items, new_items, updated_items, failed_items, error_message, finish_reason, progress, divar_count,
  resumed_from, can_resume, started_at, completed_at, created_at`.
  وضعیت‌ها: `pending|running|paused|completed|failed|cancelled` (برچسب فارسی در `app.js:5502-5505`).
- **پولینگ:** `startJobPolling()`/`stopJobPolling()` (`5458`–۵۴۶۹, هر **۵۰۰۰ms**، فقط وقتی بخش «اسکرپر» باز است)
  → `_pollJobs()` (`5471`–۵۴۹۲) که فقط جدول را re-render می‌کند و اگر آیتم تازه‌ای اضافه شده یا جابی تازه تمام
  شده، `loadProperties()` را هم صدا می‌زند (برای این‌که لیست املاک هم به‌روز شود).
- عملیات هر ردیف:
  - **گزارش این اسکرپ** (`showJobLog()` `13842`–۱۳۸۸۱`) → `GET /api/scraper/jobs/{id}/events?level=&limit=500` →
    `{ job_id, count, items: [{id, level, stage, message, details, created_at}] }`.
  - **آگهی‌های ردشده** (`showSkipped()` `13897`–۱۳۹۲۳`, فیلتر دلیل `filterSkipped()` `13947`, بازاسکرپ همه
    `rescrapeAllSkipped()` `14017`) → `GET /api/scraper/jobs/{id}/skipped?reason=&limit=1000` →
    `{ job_id, count, by_reason: {key: {label, count}}, items: [{id, divar_id, url, title, reason,
    reason_label, detail, created_at}] }`.
    بازاسکرپ‌شان با `POST /api/scraper/rescrape` (نیاز `require_verified_phone`) body: `{ urls: string[],
    label?: string }` → یک جاب واحد.
  - **تعویض شماره** (فقط اگر `job.owner_user_id === _currentUser.id` و وضعیت running/paused/pending) —
    `switchJobAccount()` (`3679`–۳۷۲۰) → `POST /api/scraper/jobs/{id}/switch-account` (نیاز
    `require_verified_phone`) body: `{ phone?: string }` (خالی = «کم‌مصرف‌ترین بعدیِ من») →
    `{ success, requested, message }`. اجرا زنده متوقف نمی‌شود؛ در اولین فرصت (یا فوری اگر پای OTP پارک باشد)
    عوض می‌شود.
  - **لغو** (وضعیت running/paused/pending) — `cancelJob()` (`5201`–۵۲۱۷) → `POST /api/scraper/jobs/{id}/cancel`
    → `{ message, was, otp_cleared }` (اگر منتظر OTP بود، آن پرامپت هم پاک می‌شود).
  - **ادامه** (اگر `can_resume`) — `resumeJob()` (`5191`–۵۱۹۹) → `POST /api/scraper/jobs/{id}/resume` (نیاز
    `require_verified_phone`) → جاب تازه با `resumed_from` ست‌شده؛ فقط صاحب جاب یا root/super_admin.
  - **حذف** (وضعیت completed/failed/cancelled) — `deleteJob()` (`5219`–۵۲۳۲) → `DELETE /api/scraper/jobs/{id}`.
    فقط لاگ/skipped/ردیف جدول پاک می‌شود؛ آگهی‌های ذخیره‌شده دست‌نخورده می‌مانند.

### ۱.۴ پنجرهٔ OTP دیوار (`divarOtpModal`, `5146`–۵۲۰۰) — قلب تجربهٔ اسکرپر
- **پولینگ:** `startOtpPolling()` هر **۴۰۰۰ms** (`5448`–۵۴۵۵) → `pollDivarOtp()` (`5741`–۵۷۷۹) →
  `GET /api/scraper/otp-pending` → `{ forwarders: {phone_digits: {online, battery, ...}}, pending: [{key,
  phone_hint, remaining}], timeout: int(seconds), identity_required: [{phone, text}] }`.
  **این endpoint خودش فقط رویدادهای متعلق به کاربر را برمی‌گرداند** (`_my_prompts` در `scraper.py:1117-1133`):
  یا شماره مال همین کاربر است، یا جاب مال همین کاربر است، یا (اگر هیچ‌کدام معلوم نبود) کاربر root/super_admin است.
- اگر `identity_required` غیرخالی باشد، اول دیالوگ «دیوار احراز هویت می‌خواهد» باز می‌شود
  (`_showIdentityWall()` ۵۷۰۱–۵۷۲۴؛ کد ملی — اسکرپر نمی‌تواند این را حل کند، فقط اعلام می‌کند و آن شماره را کنار
  می‌گذارد). تأیید انجام‌شدن → `POST /api/auth/cookies/{cookie_id}/identity-cleared` (`_identityCleared()` ۵۷۲۶).
- کد شش‌رقمی segmented (`#otp2-boxes`)، auto-submit وقتی همه پر شدند (`initOtp2Boxes()` ۵۶۵۸–۵۶۸۶).
  ارسال: `submitDivarOtp()` (`5853`–۵۸۸۰) → `POST /api/scraper/otp/{key}` body `{ "code": string }` →
  `{ success: true }` (۴۰۴ اگر منقضی/متعلق به کاربر دیگر — پیام آن دقیقاً یکسان با «موردی نیست» تا هویت
  درخواست‌های دیگران فاش نشود).
- تایمر شمارش‌معکوس از `remaining` یا `timeout` سرور می‌آید، نه عدد ثابت کلاینت (`_otp2StartTimer()` ۵۶۲۸–۵۶۵۲).
- **ارسال دوباره کد** — `resendDivarOtp()` (`5813`–۵۸۳۸) → `POST /api/scraper/otp/{key}/resend` → قفل ۱۵ ثانیه‌ای
  دکمه سمت کلاینت.
- **«گوشی در دسترس نیست — تعویض شماره»** — `switchFromOtp()` (`3723`–۳۷۳۸) مودال را می‌بندد، از همان
  `switchJobAccount()` بالا استفاده می‌کند، و اگر موفق شد این کلید را در `_dismissedOtpKeys` می‌گذارد تا دوباره
  باز نشود.
- **«رد کردن و ادامه بدون شماره»** — `dismissDivarOtp()` (`5840`–۵۸۵۱) → `POST /api/scraper/otp-cancel?job_id=...`
  (نه پارامتر `key` — یعنی این جاب دیگر تا آخر خودش OTP نمی‌خواهد، ولی جاب‌های دیگر تحت تأثیر قرار نمی‌گیرند).
- نشانهٔ حالت «خودکار — گوشی متصل است» / «گوشی آفلاین» / «دستی» (`_otp2SetMode()` ۵۷۸۲–۵۸۰۰) از `forwarders`
  همان پاسخ `otp-pending` می‌آید.

### ۱.۵ مودال «لاگ اسکرپر» (`scraperLogModal`, `5110`–۵۱۴۴)
پرست‌های آماده (Skipping/advertiser_type/SMS-OTP/rotate/ERROR) + جستجوی آزاد.
JS: `loadScraperLog()` (`5161`–۵۱۸۳).
Endpoint: `GET /api/stats/logs?lines=300&grep=...` — ⚠️ این زیر permission **`stats`** است نه `scraper`
(`stats.py:404-430`, gated by `require_admin` یعنی فقط root/super_admin/admin — این یعنی حتی با permission
`scraper` تنها ولی بدون `stats`، این دکمه باید مخفی/غیرفعال شود؛ پنل قدیم این تناقض permission را چک نمی‌کرد،
در پیاده‌سازی تازه توصیه می‌شود این را بررسی/اصلاح کنید).
response: `{ lines: string[], note?: string }`.

### ۱.۶ ویجت‌های مرتبط بیرون از این بخش (باید حداقل به‌صورت لینک/badge پورت شوند)
- Topbar badge سراسری «وضعیت کوکی» (`index.html:561-564`) → `checkCookieStatus()` (`5343`–۵۳۸۵)، از
  `GET /api/auth/cookies?mine=1` (**نه** `/auth/status`) محاسبه می‌شود؛ کلیک آن به بخش احراز هویت دیوار می‌رود.
- «وضعیت نشست‌های دیوار» و «وضعیت تسک‌های اسکرپر» در بخش **پایش سامانه** (`monitoring`, خارج از اسکوپ این
  چک‌لیست ولی هم‌داده با این بخش‌اند: `GET /api/monitoring/cookies`, `GET /api/monitoring/overview`) — فقط برای
  آگاهی هنگام طراحی معماری داده مشترک ذکر می‌شود.

### چک‌لیست مهاجرت — اسکرپر
- [ ] city/category picker از `GET /scraper/cities` و `GET /scraper/categories` (staff-only، بدون permission خاص)
- [ ] «از روی لینک دیوار» → `POST /scraper/parse-link` (فقط پرکردن فرم، هرگز start خودکار)
- [ ] سلکتور شمارهٔ دیوار با `mine=1`، روشن/خاموش هر شماره (`PATCH /auth/cookies/{id}` با `enabled`) و پیام
      انتقال جاب زنده (`moved_jobs`)
- [ ] فیلترهای بیشتر با auto-expand وقتی هرکدام فعال است + شمارنده در summary
- [ ] «چند آگهی هست؟» debounce ۹۰۰ms + توضیح `applied_after_scrape`
- [ ] چرخش شماره: توضیح جهت عدد (کمتر = چرخش بیشتر) عیناً
- [ ] شروع اسکرپینگ → phone-gate → `POST /scraper/start`؛ اگر کوکی نامعتبر، مودال هشدار قبل از شروع
- [ ] اسکرپ تکی → جاب واقعی، نه synchronous
- [ ] ذخیره به‌عنوان زمان‌بندی + CRUD کامل زمان‌بندی‌ها + اجرای فوری «به‌عنوان صاحبش»
- [ ] جدول جاب‌ها: پولینگ ۵s فقط وقتی صفحه باز است، توقف polling در unmount
- [ ] عملیات ردیف: گزارش، ردشده‌ها (+بازاسکرپ گروهی با فیلتر دلیل)، تعویض شماره (فقط صاحب جاب)، لغو، ادامه، حذف
- [ ] پنجرهٔ OTP: پولینگ ۴s، شمارش‌معکوس از سرور، ارسال دوباره با قفل ۱۵s، تعویض شماره از داخل مودال، رد کردن
      اسکوپ‌شده به یک جاب (نه کل سرور)، دیوار «احراز هویت با کد ملی» به‌صورت دیالوگ جدا و مقدم بر OTP
- [ ] لاگ اسکرپر با چک permission `stats` (نه فقط `scraper`)
- [ ] بدون `window.prompt/confirm/alert` در هیچ‌کدام از موارد بالا

---

## ۲) «احراز هویت دیوار» (nav key: `auth`, permission: `divar_auth`)

index.html: `1113`–۱۲۲۴ (`<div id="section-auth">`، شامل کارت root در همان div).

### ۲.۱ ورود به حساب دیوار (`1117`–۱۱۶۶) — فلوی OTP
مرحلهٔ ۱ — `#auth-login-form` (`1124`–۱۱۳۳): اینپوت شمارهٔ موبایل + «ارسال کد تأیید».
JS: `initiateLogin()` (`5882`–۵۹۲۹).
Endpoint: **`POST /api/auth/login`** — ⚠️ روی Ingress به سرویس `worker` می‌رود (نه `backend`)، چون Chromiumِ
لاگین دیوار را باز نگه می‌دارد تا مرحلهٔ verify. نیاز به `require_verified_phone` (شمارهٔ خودِ کاربر پنل).
body: `{ "phone_number": "09xxxxxxxxx" }` (pattern `^09\d{9}$`)
response (`AuthResponse`): `{ success, message, requires_code }`.
قواعد سمت سرور (`auth.py:166-253`):
- `_refuse_somebody_elses` → اگر این شماره از قبل صاحب دارد و صاحبش کاربر جاری نیست → ۴۰۳.
- اگر کاربر دیگری همین الان روی همین شماره در حال لاگین است → ۴۰۹.
- یک Chromium زنده در حافظهٔ همان پروسه نگه‌داشته می‌شود (`auth_instances`)، به همین دلیل روی k8s این سه مسیر
  باید روی **یک** پاد `worker` پین شوند — این جزئیات معماری backend است، نه چیزی که پنل صدا می‌زند، ولی روی
  طراحی network/rewrite تأثیر دارد: اگر پنل تازه پشت CDN/Edge دیگری قرار بگیرد، این سه مسیر باید هنوز مستقیم به
  همان worker برسند.

مرحلهٔ ۲ — `#auth-verify-form` (`1135`–۱۱۵۳): ۶ اینپوت segmented OTP + «تأیید و ورود» + «بازگشت — ارسال مجدد».
JS: `initOtpBoxes()` (`5942`–۵۹۹۳, auto-submit + paste)، `verifyCode()` (`6009`–۶۰۵۱)، `cancelDivarOtp()`
(`5996`–۶۰۰۷).
Endpoint: **`POST /api/auth/verify?phone_number=...`** (query param، نه body!) — هم روی `worker`.
body: `{ "code": "123456" }` (دقیقاً ۶ رقم)
response: `AuthResponse` مثل بالا (`requires_code` همیشه false).
قواعد سرور (`auth.py:256-377`):
- فقط کسی که لاگین را شروع کرده (`started_by`) می‌تواند verify کند؛ اگر بین این دو مرحله سرور ری‌استارت شده،
  ۴۰۹ با پیام «دوباره ارسال کد را بزنید».
- موفقیت → اگر کاربر پنل هنوز `divar_phone` نداشت، همین شماره **primary** او می‌شود؛ کوکی‌ها ذخیره/به‌روزرسانی
  می‌شوند؛ اگر ردیف کوکی صاحب نداشت، صاحبش کاربر جاری می‌شود (طبق قاعدهٔ محصول #۱-۲).

**دکمه‌های زیر فرم** (`1157`–۱۱۶۴):
- «بازنشانی نشست» — `refreshSession()` (`6053`–۶۰۶۸) → `POST /api/auth/refresh?phone_number=...` (روی `backend`
  عادی، نه worker). اگر شماره همین حالا در یک اسکرپ در حال استفاده است (`profile_in_use`)، بدون خطا
  `{ success: true, in_use: true, message: "..." }` برمی‌گرداند (باز نکردن دومین Chromium روی همان پروفایل).
  فقط صاحب همان شماره (۴۰۳ برای دیگران، ۴۰۴ اگر سشنی نیست).
- «خروج» — `logout()` (`6070`–۶۰۸۱) → `POST /api/auth/logout?phone_number=...` → کوکی‌ها را باطل می‌کند.

### ۲.۲ «نشست‌های ذخیره‌شده» (`1167`–۱۱۷۲, `#cookies-list`)
JS: `loadCookies()` (`6126`–۶۱۶۷)، `deleteCookie()` (`6246`–۶۲۵۶).
Endpoint: `GET /api/auth/cookies?mine=1` (همان shape بالا). هر ردیف: شماره، معتبر/منقضی + خاموش، نشان
«احراز هویت لازم» با دکمهٔ «انجام شد» (`_identityCleared()` → `POST /auth/cookies/{id}/identity-cleared`)،
سوییچ روشن/خاموش (همان `toggleDivarNumber`)، دکمهٔ حذف (`DELETE /api/auth/cookies/{id}`، فقط مالک یا
root/super_admin؛ هم ردیف دیتابیس هم فایل کوکی روی دیسک پاک می‌شود).

### ۲.۳ «افزودن نشست دستی (Import کوکی)» (`1173`–۱۱۹۵)
اینپوت شماره + textarea JSON کوکی‌ها (نمونه placeholder: `[{"name":"token","value":"...","domain":".divar.ir"}]`).
JS: `importCookies()` (`6083`–۶۱۲۴).
Endpoint: `POST /api/auth/cookies/import` (نیاز `require_verified_phone`)
body: `{ "phone_number": string, "cookies": any[] }`
response: `{ success: true, alive: true|false|null, message, expires_at }`
قواعد: شماره باید نرمال شود (`normalize_mobile`)؛ بدون کوکی `token`/AUTH_COOKIE_NAMES → ۴۰۰ با پیام راهنما؛
اگر شماره از قبل صاحب دارد و صاحبش کاربر جاری نیست → ۴۰۳ (**even root** نمی‌تواند کوکی روی شمارهٔ صاحب‌دار
دیگری جایگزین کند)؛ بعد از ذخیره سرور واقعاً با `confirm=True` از دیوار می‌پرسد و `alive` واقعی برمی‌گردد
(نه فقط «ذخیره شد»).

### ۲.۴ کارت root: «همهٔ شماره‌های دیوار و صاحبشان» (`#numbers-registry-card`, `1199`–۱۲۲۳, فقط با
`_currentUser.role === 'root'` نمایش داده می‌شود — `d-none` پیش‌فرض)
این همان «کارت مالکیتِ شماره» که در دستور کار به آن اشاره شده: **تنها جای برنامه که مالکیت شماره دستی عوض
می‌شود.**
JS: `loadNumbersRegistry()` (`6176`–۶۲۲۴)، `saveNumberOwner()` (`6226`–۶۲۴۴).
Endpoint خواندن: `GET /api/auth/registry` (فقط root؛ `_root_only`، بک‌اند خودش هم ۴۰۳ می‌دهد اگر نقش root نباشد)
response: `{ numbers: [{id, phone_number, owner_user_id, owner_name, is_valid, is_enabled, reveals,
last_checked_at, identity_required_at, in_use, suggested_owner: {id, name, why: "divar_phone"|"forwarder"}|null}],
users: [{id, name, username, role, is_active}] }` (کاربران `visitor` از لیست حذف شده‌اند).
ستون‌های جدول (`1216`–۱۲۱۹ / `6196`–۶۲۲۰): شماره، سلکت صاحب (پیش‌فرض خالی اگر بی‌صاحب)، وضعیت (معتبر/نامعتبر +
خاموش + احراز هویت + در حال اسکرپ)، افشا (`reveals`)، پیشنهاد (دکمهٔ میان‌بر که سلکت را روی
`suggested_owner` می‌گذارد؛ tooltip می‌گوید چرا: «سیم‌کارت این شماره در گوشی این کاربر است» یا «این کاربر این
شماره را شمارهٔ دیوار خود اعلام کرده»)، دکمه‌های ذخیره/حذف.
**Endpoint نوشتن (مورد تأکید دستور کار):**
`PATCH /api/auth/registry/{cookie_id}/owner`
body: `{ "owner_user_id": int }`
response: `{ success, id, owner_user_id, owner_name, changed: bool, moved_jobs: string[] }`
قواعد سرور (`auth.py:866-918`):
- فقط root (۴۰۳ برای همه‌ی بقیه، حتی super_admin).
- کاربر مقصد نباید `visitor` باشد (۴۰۰).
- اگر واقعاً تغییر کند: `divar_phone` صاحب قبلی (اگر همین شماره بود) پاک می‌شود؛ هر جاب زندهٔ (running/paused/
  pending) صاحب قبلی روی همین شماره، درخواست تعویض شماره می‌گیرد (`moved_jobs`)؛ در audit log با
  `audit.record("divar_number_owner_change", ...)` و یک خط `logger.warning` سطح audit ثبت می‌شود.
- دکمهٔ تأیید UI باید صراحتاً بگوید: «شمارهٔ X از این پس فقط در اختیار Y است» + «اگر اسکرپی از صاحب قبلی روی
  این شماره در حال اجراست، به شمارهٔ دیگری از خودش منتقل می‌شود» (متن دقیق `saveNumberOwner()`، `6231`–۶۲۳۳).

### چک‌لیست مهاجرت — احراز هویت دیوار
- [ ] فلوی OTP دو مرحله‌ای: `POST /auth/login` سپس `POST /auth/verify?phone_number=` (query!) — هر دو باید از
      طریق مسیری بروند که به `worker` می‌رسد (نه صرفاً `backend`)
- [ ] پنجرهٔ OTP باید از همان الگوی بصری «ظاهر پنجرهٔ OTP» استفاده کند (قاعدهٔ محصول #۳)، نه فرم ساده
- [ ] بازنشانی نشست: مدیریت حالت خاص «در حال استفاده در اسکرپ زنده» بدون نمایش خطا
- [ ] خروج، فقط صاحب شماره
- [ ] لیست نشست‌ها با `mine=1`، سوییچ روشن/خاموش، نشان «احراز هویت لازم» + دکمهٔ رفع، حذف
- [ ] Import کوکی: phone-gate، اعتبارسنجی JSON سمت کلاینت، نمایش دقیق سه حالت `alive: true/false/null`
- [ ] کارت root: فقط برای role === root رندر شود (نه فقط مخفی با CSS — چک هم در روت‌گارد صفحه لازم است)
- [ ] `PATCH /auth/registry/{id}/owner` تنها مسیر تغییر مالکیت در کل پنل — هیچ اکشن دیگری (ورود، import کوکی،
      ثبت فورواردر) نباید مالکیت را دست‌کاری کند
- [ ] پیام تأیید قبل از تغییر مالکیت باید هشدار انتقال جاب زنده را بدهد
- [ ] Topbar badge وضعیت کوکی (سراسری، خارج از این صفحه ولی از همین داده) هم باید در شل تازه ساخته شود

---

## ۳) «پراکسی‌ها» (nav key: `proxies`, permission: `proxies`)

index.html: `1534`–۱۶۲۵ (`<div id="section-proxies">`). ساده‌ترین بخش از این چهار — بدون OTP/polling خاص،
بدون مالکیت per-user (پراکسی‌ها global است، مخصوص همهٔ اسکرپ‌ها).

### ۳.۱ «افزودن پراکسی» (`1536`–۱۵۷۴)
فرم: آدرس IP، پورت، پروتکل (http/https/socks5)، نام‌کاربری/رمز اختیاری.
JS: `addProxy(e)` (`6981`–۷۰۰۳).
Endpoint: `POST /api/proxies` body (`ProxyCreate`): `{ address: str, port: int, protocol: "http"|"https"|"socks5"
= "http", username?: str, password?: str }`
response (`ProxyResponse`): `{ id, address, port, protocol, is_active, is_working, fail_count, success_count,
avg_response_time, last_checked }`.
قواعد سرور: آدرس باید public باشد (`net_guard.resolve_public` — IP داخلی/loopback رد می‌شود، ۴۰۰)؛
تکراری (همان address+port) → ۴۰۰.

### ۳.۲ «وارد کردن دسته‌ای» (`1576`–۱۵۸۶)
textarea چندخطی: `ip:port` یا `ip:port:user:pass` در هر خط.
JS: `importProxies()` (`9293`–۹۳۱۴).
Endpoint: `POST /api/proxies/import` body: `{ proxy_list?: str, url?: str, test: bool=true }` (فرمت‌های پذیرفته:
`ip:port`, `ip:port:user:pass`, یا `scheme://[user:pass@]host:port`؛ همچنین می‌تواند از یک URL لیست را fetch کند —
UI فعلی فقط textarea را دارد، فیلد `url` را استفاده نمی‌کند اما بک‌اند پشتیبانی می‌کند).
response: `{ imported, skipped, refused, tested: {...}|null }` (`refused` = آدرس‌های private/internal که اصلاً
وارد جدول نشدند؛ ردیف‌های imported با `is_working=false` شروع می‌شوند تا probe واقعی آن‌ها را تأیید کند).

### ۳.۳ «لیست پراکسی‌ها» (`1589`–۱۶۲۳)
هدر: «تست همه»، «حذف همه» (`#proxy-wipe`)، بروزرسانی.
ستون‌ها: آدرس، پورت، وضعیت (فعال/غیرفعال بر اساس `is_working`)، خروجی (کشور خروجی + دیتاسنتر/خانگی، با هشدار
اگر IR نباشد)، موفق/ناموفق، زمان پاسخ، عملیات.
JS: `loadProxies()` (`6927`–۶۹۷۹)، `_proxyExitCell()` (`6293`–۶۳۰۱).
Endpoint: `GET /api/proxies?active_only=false` → `ProxyList { items: ProxyResponse[], total }`.
**نکتهٔ مهم UX:** «فعال» فقط یعنی دیوار جواب داد؛ کشور خروجی مهم‌تر است — اگر `exit_country !== 'IR'`
بج قرمز با توضیح «برای دیوار مناسب نیست» (پراکسی خارجی برای دیوار قابل‌قبول نیست، حتی اگر تست موفق باشد).
باید فیلد `exit_country`, `exit_ip`, `is_hosting` هم در response موجود باشد (schema فعلی `ProxyResponse` این‌ها
را ندارد ولی `test`/`test-all` آن‌ها را برمی‌گردانند و روی رکورد ذخیره می‌شوند — این را موقع پورت با
type دقیق response بررسی کنید که آیا `GET /proxies` هم این فیلدها را برمی‌گرداند یا فقط بعد از تست جدا).
- عملیات هر ردیف:
  - **تست** — `testProxy(id)` (`7005`–۷۰۲۰) → `POST /api/proxies/{id}/test` → خروجی probe (`success,
    response_time, exit_country, exit_ip, is_hosting, error?`).
  - **روشن/خاموش** — `toggleProxy(id)` (`7022`–۷۰۳۰) → `POST /api/proxies/{id}/toggle` →
    `{ success, is_active, message }`.
  - **حذف** — `deleteProxy(id)` (`7032`–۷۰۴۲) → `DELETE /api/proxies/{id}`.
- **«تست همه»** — `testAllProxies()` (`7044`–۷۰۵۳) → `POST /api/proxies/test-all` → `{ total, working, iranian,
  results: [...] }` (فقط پراکسی‌های `is_active=true` تست می‌شوند).
- **«حذف همه»** — `deleteAllProxies()` (`6270`–۶۲۸۵؛ توجه: در فایل جاش با توابع auth قاطی است، نه کنار بقیهٔ
  proxy functions) → `DELETE /api/proxies?confirm_count=N` (پارامتر query الزامی؛ `N` باید دقیقاً برابر تعداد
  فعلی نمایش‌داده‌شده باشد وگرنه سرور ۴۰۹ می‌دهد — «صفحه را تازه کنید» — محافظت در برابر race با یک تب دیگر).
  response: `{ success: true, deleted: N }`.

### چک‌لیست مهاجرت — پراکسی‌ها
- [ ] فرم افزودن با اعتبارسنجی سمت سرور برای آدرس public (خطای ۴۰۰ باید پیام فارسی net_guard را نشان دهد)
- [ ] Import دسته‌ای با پشتیبانی از سه فرمت رشته (در توضیح placeholder بماند)
- [ ] جدول با بج کشور خروجی + هشدار غیر-IR؛ تفکیک دیتاسنتر/خانگی
- [ ] «حذف همه» با الگوی `confirm_count` (نه صرفاً یک confirm ساده — باید تعداد لحظهٔ کلیک را بفرستد)
- [ ] توجه: این بخش permission-based است نه ownership-based؛ همهٔ نقش‌هایی که `proxies` permission دارند همه‌چیز
      را می‌بینند و مدیریت می‌کنند (برخلاف بخش‌های ۱ و ۲ که per-owner است)

---

## ۴) «فرستندهٔ پیامک» (nav key: `forwarder`, permission: `forwarder`)

index.html: `1457`–۱۵۳۲ (`<div id="section-forwarder">`).
قاعدهٔ محصول ویژه: این permission جدا از `divar_auth` است («یک نفر می‌تواند به یکی اعتماد شود و به دیگری نه» —
`app/auth/permissions.py:43-46`) ولی از نظر مالکیت **کاملاً وابسته به همان قاعدهٔ شمارهٔ دیوار** است: یک سیم‌کارت
فورواردر ادعای مالکیت روی آن شماره است.

### ۴.۱ «گوشی‌های من» (`1459`–۱۴۸۲)
جدول: نام، سیم‌کارت‌ها (اول/دوم)، وضعیت، آخرین کد، تعداد کد، عملیات.
JS: `loadForwarders()` (`6431`–۶۴۷۶)، `_fwSimsCell()` (`6478`–۶۴۸۴).
Endpoint: `GET /api/forwarder/devices` → `{ devices: [{...to_dict(), health}], count }`
`health` (از `app/services/forwarder.py`، تابع `fw.health(d)`): `{ state: "ok"|"offline"|"never_seen"|
"no_codes_yet"|"disabled", message_fa, seconds_since_code }` — نگاشت رنگ/برچسب در `FW_STATE`
(`app.js:6415-6421`).
- **افزودن گوشی** — `addForwarderDevice()` (`6548`–۶۵۸۳)، سه پرامپت متنی پشت‌سرهم (اسم گوشی، سیم اول الزامی،
  سیم دوم اختیاری برای گوشی دو-سیم‌کارته).
  Endpoint: `POST /api/forwarder/devices` (نیاز `require_verified_phone`)
  body: `{ label?: str(≤80), sim_phone?: str(≤20), sim_phone2?: str(≤20), note?: str }`
  response: `{ ...device.to_dict(reveal_secret=true), health }` — **سکرت کامل فقط همین یک‌بار و در
  `/config` برمی‌گردد**، هیچ‌جای دیگر (حتی لیست) کامل نشان داده نمی‌شود.
  قواعد سرور (`forwarder.py:80-161`): دو سیم یک گوشی نمی‌توانند یک شماره باشند (۴۰۰)؛ یک سیم نمی‌تواند در دو
  دستگاه فعال ثبت شود (۴۰۹ اگر مال خودش، ۴۰۳ اگر مال کاربر دیگر)؛ سیمی که در `Cookie.owner_user_id` صاحب دیگری
  دارد هم رد می‌شود (۴۰۳) — یعنی نمی‌توانی شمارهٔ دیوار یک هم‌کار را روی فورواردر خودت ثبت کنی.
- **راهنما** — `fwGuide(id)` (`6687`–۶۸۳۷)
  Endpoint: `GET /api/forwarder/devices/{id}/config` → payload کامل: `{ device: {...reveal_secret}, setup_payload
  (deep-link "sorinflow://setup?..."), android_apk_url, android_apk_version, android_source_url,
  android_release_url, ios: {available:false, message_fa}, endpoints: {inbound, heartbeat}, headers: {User-agent,
  X-Forwarder-Id, X-OTP-Secret}, rules: [{name_fa, sender:"*", text_filter, template, why_fa}, ...×2], rules_sim2:
  [...×2 یا خالی], accounts: string[], advanced_fa: {retries:10, store_failed:true, ignore_ssl:false, note} }`.
  محتوای راهنما (باید عیناً پورت شود، قدم‌به‌قدم):
  1. **دانلود APK** — `android_apk_url` = `https://sorinflow.com/downloads/sorinflow-forwarder.apk` (mirror از
     GitHub release، چون گیت‌هاب از ایران کند/فیلتر است — `app.services.apk_mirror`)؛ لینک GitHub هم به‌عنوان
     جایگزین؛ توضیح صریح که برنامه در گوگل‌پلی نیست (چون پیامک می‌خواند) و Play Protect هشدار می‌دهد.
  2. مجوز خواندن پیامک + مسیر «Allow restricted settings» برای اندروید ۱۳+.
  3. غیرفعال‌کردن محدودیت باتری/Autostart (مهم‌ترین قدم، با `fw-warn`).
  4. QR راه‌اندازی — **کد QR پیش‌فرض پنهان است** پشت یک shield با کلیک برای نمایش
     (`fwRevealQr()`/`_fwUncover()`، `6854`–۶۸۷۹) و فقط **۳۰ ثانیه** (`FW_QR_REVEAL_SECONDS=30`) نمایان می‌ماند،
     چون سکرت دستگاه را دارد. کپی لینک راه‌اندازی هم جدا موجود است (`fwCopyPayload()`).
     زیر QR: فیلدهای دستی برای برنامهٔ عمومی SMS Forwarder — فرستنده `*`، فیلتر متن، آدرس Webhook، هدرها
     (JSON، شامل سکرت — هشدار «با کسی به اشتراک نگذارید»)، قالب JSON Payload.
  5. دکمهٔ «Send test to server» در اپ، سپس دکمهٔ «بررسی اتصال» اینجا.
  6. قانون دوم (کد ورود) + در صورت گوشی دوسیم‌کارته، دو قانون اضافه برای SIM 2.
  **تازه‌سازی خودکار QR** — اگر بین باز بودن مودال، سکرت روچرخش کند یا شماره عوض شود، `fwRefreshQr()`
  (`6889`–۶۹۰۲) با مقایسهٔ `setup_payload` قدیم/جدید، راهنما را از نو می‌سازد (بدون رفرش صفحه).
- **بررسی اتصال** — `fwTest(id)` (`6585`–۶۵۹۱) → `POST /api/forwarder/devices/{id}/test` →
  `{ ok, health, hint_fa, checked_at }`. ⚠️ این یک **درخواست به خود گوشی نمی‌فرستد** — فقط می‌گوید آخرین باری که
  گوشی خودش تماس گرفته چه زمانی بوده (توضیح دقیق در کامنت بک‌اند: «nothing can make a handset speak»).
- **کلید تازه** — `fwRotate(id)` (`6593`–۶۶۰۴) → `POST /api/forwarder/devices/{id}/rotate` → سکرت قدیم فوراً
  از کار می‌افتد؛ راهنما با سکرت تازه از نو باز می‌شود.
- **ویرایش شمارهٔ سیم ۱/۲** — `fwEditPhone(id, slot)` (`6606`–۶۶۳۲) → `PATCH /api/forwarder/devices/{id}` body:
  `{ sim_phone?: str }` یا `{ sim_phone2?: str }` (یکی از این دو، بسته به slot).
  عمومی‌تر `DeviceEdit`: `{ label?, sim_phone?, sim_phone2?, note?, is_active? }`.
- **حذف** — `fwDelete(id)` (`6634`–۶۶۵۲) → `DELETE /api/forwarder/devices/{id}`.

### ۴.۲ «کدهای رسیده از گوشی» (`1484`–۱۵۱۰)
فیلتر: همه / «به اسکرپر داده شد» (`matched`) / «زودتر رسید — نگه داشته شد» (`parked_early`) / «مشکل‌دار».
JS: `loadForwarderLog()` (`6514`–۶۵۴۶)، `_fwLatency()` (`6503`–۶۵۱۲، محاسبهٔ زمان رفت‌وبرگشت با محافظت در برابر
ساعت گوشیِ غلط — بیش از ۵ دقیقه یا منفی = «ساعت گوشی»، نه یک عدد گمراه‌کننده).
Endpoint: `GET /api/sms/events?limit=100&stage=inbound` (⚠️ این روتر زیر permission **`sms`** ثبت شده در
`__init__.py` (`router.include_router(sms.router, ..., dependencies=_perm("sms"))`) — یعنی این endpoint فقط با
لاگین معتبر قابل دسترس است (`Depends(get_current_user)` داخل خودِ endpoint) و از نظر router-level تنها زیر
permission `sms` گیت شده، **نه** `forwarder`. برای پنل تازه این را دقیقاً چک کنید: کسی که فقط `forwarder`
permission دارد ولی `sms` ندارد ممکن است این لاگ را نبیند — یا باید permission را بازبینی کرد یا مطابق فعلی
پیاده‌سازی کرد).
response: `{ events: [{id, at, stage, level, message, route, status, actor, details}], count }` — پنل با
`e.details.reason` فیلتر می‌کند (`FW_REASON` نگاشت به فارسی: matched/parked_early/stale_code/no_code_in_text/
no_pending_for_account/already_answered/test).

### ۴.۳ دستگاه‌های ماشینی (برای مستندسازی کامل — نه چیزی که پنل مستقیم صدا می‌زند)
دو endpoint HMAC-signed که خودِ اپ اندروید صدا می‌زند (نه پنل، ولی باید در طراحی داده حساب شوند):
`POST /api/scraper/otp-inbound` (کد پیامک) و `POST /api/scraper/forwarder-heartbeat` (سلامت گوشی).
این‌ها روی `scraper.machine_router` بدون permission ثبت شده‌اند (احراز هویت‌شان HMAC داخل خودِ route است، نه
لاگین کاربر) — پنل نباید مستقیماً این‌ها را صدا بزند.

### چک‌لیست مهاجرت — فرستندهٔ پیامک
- [ ] جدول گوشی‌ها با health state رنگی + آخرین کد + تعداد کد
- [ ] افزودن گوشی: phone-gate، دو پرامپت شماره (اول الزامی، دوم اختیاری)، چک تداخل سیم با دستگاه/شمارهٔ دیگران
- [ ] راهنمای نصب قدم‌به‌قدم کامل (۶ قدم) با کپی تک‌کلیکی هر فیلد
- [ ] دانلود APK از mirror داخلی سایت (نه فقط لینک مستقیم GitHub) + نسخهٔ فعلی + لینک GitHub جایگزین
- [ ] QR پنهان پشت shield، نمایش موقت ۳۰ ثانیه‌ای، auto-refresh اگر سکرت/شماره عوض شد
- [ ] «کلید تازه» با هشدار صریح که گوشی تا وارد کردن سکرت تازه کار نمی‌کند
- [ ] ویرایش شماره سیم ۱/۲ با هشدار که QR/راهنما باید دوباره اسکن شود
- [ ] «بررسی اتصال» را به‌عنوان «آخرین خبر از گوشی» نمایش دهید، نه یک ping زنده
- [ ] لاگ کدهای رسیده با فیلتر دلیل + محاسبهٔ latency محافظت‌شده در برابر ساعت غلط گوشی
- [ ] بررسی/رفع تناقض permission بین `forwarder` (خود بخش) و `sms` (لاگ کدها از `/sms/events` می‌آید)

---

## پیوست: منابع دقیق کد (برای رفرنس سریع هنگام پیاده‌سازی)

| بخش | index.html | app.js (توابع کلیدی) | روت‌های بک‌اند |
|---|---|---|---|
| اسکرپر | `804-1111`, مودال‌ها `4096-4121`, `5063-5092`, `5108-5200` | `3310-4235`, `5156-5233`, `5282-5960 (OTP)`, `13842-14030` | `app/api/routes/scraper.py` |
| احراز هویت دیوار | `1113-1224` | `5343-6167 (auth+cookies)`, `6169-6256 (registry)` | `app/api/routes/auth.py` |
| پراکسی‌ها | `1534-1625` | `6270-6301`, `6927-7053`, `9293-9314` | `app/api/routes/proxies.py` |
| فرستندهٔ پیامک | `1457-1532` | `6407-6926` | `app/api/routes/forwarder.py` (+ `scraper.py` مسیرهای `otp-inbound`/`forwarder-heartbeat`، `sms.py` برای لاگ) |

permission wiring: `app/api/routes/__init__.py:48-68`. تعریف permissionها: `app/auth/permissions.py:37-53`.
gate شمارهٔ موبایل: `app/auth/dependencies.py:128-207`.
