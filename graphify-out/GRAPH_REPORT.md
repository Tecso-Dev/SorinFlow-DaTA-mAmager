# Graph Report - .  (2026-07-10)

## Corpus Check
- 79 files · ~77,261 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2169 nodes · 5375 edges · 89 communities (66 shown, 23 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 464 edges (avg confidence: 0.64)
- Token cost: 14,000 input · 4,500 output

## Community Hubs (Navigation)
- Chart.js Vendor Internals
- Divar Scraping Engine
- Chart.js Core
- Backend API Routes & Database
- Chart.js Scales & Drawing
- Bootstrap Vendor Bundle
- Pydantic Schemas
- Divar Auth & Stealth
- Dashboard UI Shell
- Chart.js Datasets
- Chart.js Elements & Updates
- Captcha Solver & Tests
- Dashboard API Client
- Property Data Validator
- Chart.js Ticks & Scales
- Bootstrap Components
- Bootstrap Carousel & Events
- User Auth & TOTP
- Chart.js Helpers
- Chart.js Drawing Primitives
- CRM Leads & Notifications
- Scraper Job API
- main.py
- Backend Deployment (sorinflow.com, NodeP…
- cs
- a()
- decode_token()
- an()
- crm.py
- bt
- Settings
- CategoryValidator
- proxies.py
- Ks
- AsyncSession
- tn
- ._queueCallback()
- we()
- loadLeads()
- on()
- eo()
- _ssh.py
- parsers.py
- remove()
- qi
- ao
- verifyCode()
- W
- cn
- jn
- get-docker.sh
- normalize_persian_digits()
- parse_persian_number()
- parse_price_with_unit()
- .notifyPlugins()
- .getDatasetMeta()
- get_system_health()
- extract_property_details()
- executeBulkScraping()
- us
- Customer
- loadDashboard()
- loadProperties()
- ra()
- DailyPerformance
- crm_models.py
- send_sms()
- qn
- extract_divar_id()
- .buildOrUpdateControllers()
- rs
- otp_store.py
- test_parsers.py
- ._createDescriptors()
- extract_price_info()
- deploy_remote.py
- initApp()
- download_property_images()
- Q
- .save_property()
- Y
- bootstrap.sh
- conftest.py
- server-setup.sh
- __init__.py
- renew-ssl.sh
- start.sh
- Kubernetes Namespace: sorinflow

## God Nodes (most connected - your core abstractions)
1. `apiCall()` - 82 edges
2. `va` - 73 edges
3. `showToast()` - 70 edges
4. `an()` - 63 edges
5. `n()` - 62 edges
6. `s()` - 58 edges
7. `ns()` - 57 edges
8. `DivarScraper` - 54 edges
9. `o()` - 51 edges
10. `a()` - 49 edges

## Surprising Connections (you probably didn't know these)
- `Persian Install Guide (Docker-only setup)` --semantically_similar_to--> `Persian Dashboard User Guide`  [INFERRED] [semantically similar]
  INSTALL.md → README.fa.md
- `Python Dependency Manifest` --conceptually_related_to--> `SorinFlow Divar Scraper`  [INFERRED]
  requirements.txt → README.md
- `FastAPI Backend Service (sorinflow_backend)` --references--> `Python Dependency Manifest`  [INFERRED]
  docker-compose.yml → requirements.txt
- `TestCitiesAndCategories` --uses--> `Settings`  [INFERRED]
  tests/test_config.py → app/config.py
- `TestSettings` --uses--> `Settings`  [INFERRED]
  tests/test_config.py → app/config.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Docker Compose Local Stack** — docker_compose_db_service, docker_compose_redis_service, docker_compose_backend_service, docker_compose_nginx_service [EXTRACTED 1.00]
- **Kubernetes Production Stack (sorinflow.com)** — k8s_00_namespace_sorinflow_namespace, k8s_02_postgres_postgres_statefulset, k8s_03_redis_redis_statefulset, k8s_04_backend_backend_deployment, k8s_05_ingress_sorinflow_ingress, k8s_06_traefik_acme_letsencrypt_resolver [EXTRACTED 1.00]
- **CI/CD Deployment Flow (ghcr → k3s)** — github_workflows_deploy_build_job, github_workflows_deploy_ghcr_image, github_workflows_deploy_deploy_job, k8s_04_backend_backend_deployment [EXTRACTED 1.00]

## Communities (89 total, 23 thin omitted)

### Community 0 - "Chart.js Vendor Internals"
Cohesion: 0.05
Nodes (52): As(), b(), beforeUpdate(), bo, buildTicks(), cn(), dn(), e() (+44 more)

### Community 1 - "Divar Scraping Engine"
Cohesion: 0.04
Nodes (43): ContactExtractor, Path, Extracts phone numbers from a Divar listing page., DivarScraper, Any, datetime, Scrape detailed information from a property page.          target_category: if p, Initialize scraper with browser and optional session restoration (+35 more)

### Community 2 - "Chart.js Core"
Cohesion: 0.04
Nodes (23): addBox(), beforeDatasetDraw(), beforeDatasetsDraw(), configure(), d(), da(), destroy(), Di() (+15 more)

### Community 3 - "Backend API Routes & Database"
Cohesion: 0.05
Nodes (53): SorinFlow Divar Scraper - API Package, CookieImportRequest, delete_cookie(), import_cookies(), list_cookies(), BaseModel, SorinFlow Divar Scraper - Authentication API Routes, List all stored cookie sessions (+45 more)

### Community 4 - "Chart.js Scales & Drawing"
Cohesion: 0.06
Nodes (22): afterDraw(), afterEvent(), afterUpdate(), Bi(), Ci(), f(), Fi(), g() (+14 more)

### Community 5 - "Bootstrap Vendor Bundle"
Cohesion: 0.07
Nodes (54): Ae(), be(), Ce(), D(), De(), di(), $e(), Ee() (+46 more)

### Community 6 - "Pydantic Schemas"
Cohesion: 0.05
Nodes (62): get_cookie_status(), initiate_login(), AsyncSession, Get current cookie/session status, Initiate login with phone number, Verify OTP code and complete login, verify_otp(), delete_property() (+54 more)

### Community 7 - "Divar Auth & Stealth"
Cohesion: 0.05
Nodes (38): logout(), Attempt to refresh/validate session, Invalidate stored cookies and logout, refresh_session(), DivarAuth, Any, AsyncSession, Path (+30 more)

### Community 8 - "Dashboard UI Shell"
Cohesion: 0.05
Nodes (49): addFollowupRow(), addShowingRow(), _applyCrmRoleVisibility(), applyRoleUI(), clearScraperDate(), clearToken(), closeSidebar(), CONTACT_TYPE_LABELS (+41 more)

### Community 9 - "Chart.js Datasets"
Cohesion: 0.05
Nodes (7): bn, initialize(), labelColor(), labelPointStyle(), ns(), rt(), updateRangeFromParsed()

### Community 10 - "Chart.js Elements & Updates"
Cohesion: 0.06
Nodes (26): Ae(), beforeLayout(), ca(), _calculateBarIndexPixels(), _calculateBarValuePixels(), getBasePixel(), getLabelAndValue(), getLabelForValue() (+18 more)

### Community 11 - "Captcha Solver & Tests"
Cohesion: 0.06
Nodes (26): PuzzleCaptchaSolver, Strategy 5 — run edge matching at ×0.75, ×1.0, ×1.25 scales.          Accounts f, Return x-offset (pixels) for the slider, or None on failure.          Runs up to, Crop to bounding box of non-white pixels (fast numpy path)., Canny edge map as 3-channel image., CLAHE contrast enhancement — makes gap shadow more visible., Run matchTemplate; return (x_pos, confidence)., Strategy 1 — edge maps (original approach, improved threshold). (+18 more)

### Community 12 - "Dashboard API Client"
Cohesion: 0.10
Nodes (48): apiCall(), _collectCustomerPayload(), copyTotpSecret(), createUser(), deleteContact(), deleteCustomer(), deleteDeal(), deleteDpa() (+40 more)

### Community 13 - "Property Data Validator"
Cohesion: 0.07
Nodes (21): PropertyDataValidator, Any, Property Data Validator - Validates extracted property data Smart validation wit, Validate sale property specific fields, Validation result for a property, Validate common property fields, Validate property data extracted from Divar     Hybrid approach with multiple va, Check if rent price is in valid range (+13 more)

### Community 14 - "Chart.js Ticks & Scales"
Cohesion: 0.07
Nodes (12): buildLookupTable(), En, Fo(), _generate(), getDecimalForValue(), _getTimestampsForTable(), init(), initOffsets() (+4 more)

### Community 15 - "Bootstrap Components"
Cohesion: 0.07
Nodes (4): H, st, ui(), Qi()

### Community 16 - "Bootstrap Carousel & Events"
Cohesion: 0.09
Nodes (4): Es, parents(), xt, at()

### Community 17 - "User Auth & TOTP"
Cohesion: 0.10
Nodes (36): get_recent_logs(), Return last N lines from the scraper log file (admin only)., admin_disable_totp(), create_user(), delete_user(), get_me(), list_users(), login() (+28 more)

### Community 18 - "Chart.js Helpers"
Cohesion: 0.08
Nodes (12): ri(), ce(), color(), de, Ee(), he(), It(), qt() (+4 more)

### Community 19 - "Chart.js Drawing Primitives"
Cohesion: 0.09
Nodes (32): ai(), ao(), average(), beforeDraw(), dataset(), draw(), getCenterPoint(), hi() (+24 more)

### Community 20 - "CRM Leads & Notifications"
Cohesion: 0.10
Nodes (28): create_lead(), list_leads(), notify_lead(), create_lead_from_property(), AsyncSession, SorinFlow CRM — Lead service Converts scraped properties into CRM leads., Create a CRM lead from a newly scraped property.     Returns None if a lead for, _build_message() (+20 more)

### Community 21 - "Scraper Job API"
Cohesion: 0.08
Nodes (31): cancel_scraping_job(), get_active_tasks(), get_available_categories(), get_available_cities(), get_otp_pending(), get_scraping_job(), OtpSubmitRequest, AsyncSession (+23 more)

### Community 22 - "main.py"
Cohesion: 0.07
Nodes (29): close_db(), close_redis(), init_db(), _migrate_properties_owner_phone(), _migrate_scraping_jobs_divar_phone(), _migrate_users_divar_phone(), _migrate_users_totp(), Idempotently add divar_phone column to users table. (+21 more)

### Community 23 - "Backend Deployment (sorinflow.com, NodeP…"
Cohesion: 0.06
Nodes (34): FastAPI Backend Service (sorinflow_backend), PostgreSQL Service (sorinflow_db), Nginx Reverse Proxy (sorinflow_nginx), Redis Service (sorinflow_redis), Infinity-House Logo (favicon.svg), Admin Dashboard SPA (RTL Persian), SorinFlow Landing Page (WebGL nebula, Vazirmatn), CI Build Job (GitHub-hosted, ghcr.io push) (+26 more)

### Community 25 - "a()"
Cohesion: 0.09
Nodes (10): a(), aa(), afterDatasetsUpdate(), determineDataLimits(), dt(), getValueForPixel(), j(), ko (+2 more)

### Community 26 - "decode_token()"
Cohesion: 0.16
Nodes (13): get_current_user(), get_current_user_optional(), AsyncSession, Same as get_current_user but returns None instead of raising 401., create_access_token(), decode_token(), get_password_hash(), SorinFlow — JWT helpers + password hashing (+5 more)

### Community 27 - "an()"
Cohesion: 0.12
Nodes (3): an(), Mn(), reset()

### Community 28 - "crm.py"
Cohesion: 0.13
Nodes (24): create_deal(), create_task(), delete_deal(), delete_task(), export_deals_excel(), export_deals_json(), get_deal(), get_lead() (+16 more)

### Community 29 - "bt"
Cohesion: 0.13
Nodes (3): bt, Cs, os()

### Community 30 - "Settings"
Cohesion: 0.12
Nodes (7): Parse proxy list into individual proxies, Application settings loaded from environment variables, Settings, BaseSettings, Unit tests for app configuration., TestCitiesAndCategories, TestSettings

### Community 31 - "CategoryValidator"
Cohesion: 0.12
Nodes (16): CategoryValidator, PropertyType, any, Category Validator - Smart Category Detection & Filtering Handles category valid, Validate اگه یک listing واقعا فروش مسکن هستش, Check اگه property type معتبر و درست identify شده, Extract category hints از listing         برای بیشتر اطمینان pattern matching, Filter listings by category         Returns: (valid_listings, invalid_listings) (+8 more)

### Community 32 - "proxies.py"
Cohesion: 0.14
Nodes (22): create_proxy(), delete_proxy(), get_proxies(), get_proxy(), import_proxies(), ProxyImportRequest, AsyncSession, BaseModel (+14 more)

### Community 33 - "Ks"
Cohesion: 0.19
Nodes (3): getElementFromSelector(), Ks, Hs

### Community 34 - "AsyncSession"
Cohesion: 0.14
Nodes (19): create_contact(), create_note(), crm_stats(), delete_contact(), delete_lead(), delete_note(), export_contacts_excel(), export_contacts_json() (+11 more)

### Community 36 - "._queueCallback()"
Cohesion: 0.17
Nodes (4): Bt, getSelectorFromElement(), Ft(), jt()

### Community 37 - "we()"
Cohesion: 0.13
Nodes (11): be(), ct(), fs(), ge(), ms(), pe(), ps(), vs() (+3 more)

### Community 38 - "loadLeads()"
Cohesion: 0.15
Nodes (18): clearLeadsDateFilter(), clearLeadsFilter(), deleteLead(), _dpaScoreParts(), gregorianToJalali(), _initLeadsDatePickers(), jalaliToGregorian(), loadCrmStats() (+10 more)

### Community 40 - "eo()"
Cohesion: 0.15
Nodes (5): Do(), eo(), Gn(), Oe(), so()

### Community 41 - "_ssh.py"
Cohesion: 0.15
Nodes (15): main(), Full server provisioning for SorinFlow on a fresh Ubuntu + k3s box.  Steps:   1., sh(), shq(), capture(), connect(), put(), SSHClient (+7 more)

### Community 42 - "parsers.py"
Cohesion: 0.14
Nodes (13): enrich_price_from_features(), _extract_list_data(), extract_location(), extract_rooms_from_text(), _is_year_value(), parse_listing_card(), Any, Pure parsing helpers for Divar property data. All functions are stateless — no b (+5 more)

### Community 46 - "verifyCode()"
Cohesion: 0.17
Nodes (15): cancelDivarOtp(), checkAuthStatus(), checkCookieStatus(), checkDivarSessionBanner(), _clearOtpBoxes(), deleteCookie(), _getActiveSession(), _getOtpCode() (+7 more)

### Community 48 - "cn"
Cohesion: 0.29
Nodes (3): cn, ln(), rn()

### Community 50 - "get-docker.sh"
Cohesion: 0.33
Nodes (12): check_forked(), command_exists(), deprecation_notice(), do_install(), echo_docker_as_nonroot(), is_darwin(), is_dry_run(), is_wsl() (+4 more)

### Community 51 - "normalize_persian_digits()"
Cohesion: 0.27
Nodes (3): normalize_persian_digits(), Convert Persian/Arabic digits to ASCII; clean ZWNJ, NBSP, etc., TestNormalizePersianDigits

### Community 52 - "parse_persian_number()"
Cohesion: 0.27
Nodes (3): parse_persian_number(), Convert a Persian/Arabic digit string to int., TestParsePersianNumber

### Community 53 - "parse_price_with_unit()"
Cohesion: 0.27
Nodes (3): parse_price_with_unit(), Parse '۹۰۰ میلیون', '۱.۸۰۰ میلیارد', 'رایگان' etc. → int (Tomans)., TestParsePriceWithUnit

### Community 56 - "get_system_health()"
Cohesion: 0.18
Nodes (12): get_dashboard_stats(), get_jobs_summary(), get_property_trends(), get_system_health(), AsyncSession, Get system health status, Get scraping jobs summary, Get dashboard statistics (cached 60s in Redis; all users see the same totals). (+4 more)

### Community 57 - "extract_property_details()"
Cohesion: 0.39
Nodes (3): extract_property_details(), Extract area, rooms, floor, amenities, etc. from a property page., TestExtractPropertyDetails

### Community 58 - "executeBulkScraping()"
Cohesion: 0.20
Nodes (12): cancelJob(), continueScraping(), executeBulkScraping(), executeSingleScraping(), _intOrNull(), loadJobs(), _pollJobs(), _renderJobsTable() (+4 more)

### Community 60 - "Customer"
Cohesion: 0.22
Nodes (10): _apply_customer_payload(), _clean_customer_rows(), create_customer(), delete_customer(), get_customer(), list_customers(), Keep only known keys per row; drop rows that are entirely empty., update_customer() (+2 more)

### Community 61 - "loadDashboard()"
Cohesion: 0.22
Nodes (11): applyTheme(), chartColors(), currentTheme(), loadDashboard(), refreshChartTheme(), _renderCrmCharts(), _renderCrmReportStats(), toggleTheme() (+3 more)

### Community 62 - "loadProperties()"
Cohesion: 0.25
Nodes (11): deleteProperty(), exportProperties(), formatNumber(), formatPrice(), goToPage(), loadProperties(), onFilterCategoryChange(), _selectedCategoryType() (+3 more)

### Community 63 - "ra()"
Cohesion: 0.18
Nodes (7): ea(), ha, la(), pa(), ra(), sa(), ua()

### Community 64 - "DailyPerformance"
Cohesion: 0.27
Nodes (8): _apply_dpa_payload(), create_dpa(), delete_dpa(), get_dpa(), list_dpa(), update_dpa(), DailyPerformance, فرم ارزیابی عملکرد روزانه (DPA) — Data-Driven Management      Mirrors the Arad d

### Community 65 - "crm_models.py"
Cohesion: 0.20
Nodes (8): create_reminder(), delete_reminder(), get_due_reminders(), list_reminders(), Reminders due in the next 24 hours that haven't been sent., SorinFlow CRM — database models Contact, Deal, Note, Task, Reminder, SmsLog, Cus, Follow-up reminder with optional SMS delivery, Reminder

### Community 66 - "send_sms()"
Cohesion: 0.24
Nodes (8): list_sms_logs(), send_sms_route(), SmsLog, SorinFlow CRM — SMS service Supports Kavenegar and Melipayamak providers., Send an SMS via the chosen provider.     Returns {"success": bool, "provider": s, _send_kavenegar(), _send_melipayamak(), send_sms()

### Community 68 - "extract_divar_id()"
Cohesion: 0.36
Nodes (3): extract_divar_id(), Extract the short listing ID from a Divar URL., TestExtractDivarId

### Community 71 - "otp_store.py"
Cohesion: 0.25
Nodes (3): In-process OTP wait/resolve store for Divar contact-info SMS verification. Backg, request(), Event

### Community 72 - "test_parsers.py"
Cohesion: 0.36
Nodes (4): generate_tag_number(), Generate a unique SF-YYYYMMDDHHMMSS-XXXXXX tag., Comprehensive tests for app/scraper/parsers.py All functions are pure — no DB, n, TestGenerateTagNumber

### Community 74 - "extract_price_info()"
Cohesion: 0.38
Nodes (3): extract_price_info(), Extract total_price / deposit / rent_price from a property page., TestExtractPriceInfo

### Community 75 - "deploy_remote.py"
Cohesion: 0.52
Nodes (6): connect(), install_pubkey(), main(), SSHClient, Remote deploy helper for sorinflow.com via paramiko (password or key auth).  Usa, run_deploy()

### Community 76 - "initApp()"
Cohesion: 0.33
Nodes (6): addProxy(), initApp(), initCityPicker(), loadCategories(), loadCities(), onScraperCategoryChange()

### Community 77 - "download_property_images()"
Cohesion: 0.40
Nodes (4): download_property_images(), Path, Image downloader for Divar property listings., Download images to <images_dir>/<divar_id>/ as JPEG and return local file paths.

### Community 81 - "bootstrap.sh"
Cohesion: 0.50
Nodes (3): DEBIAN_FRONTEND, KUBECONFIG, bootstrap.sh script

### Community 82 - "conftest.py"
Cohesion: 0.50
Nodes (3): Shared pytest fixtures for SorinFlow tests., Minimal Divar property page HTML for parser tests., sample_html_property()

## Knowledge Gaps
- **41 isolated node(s):** `Config`, `Config`, `cookieStatus`, `ROLE_NAV_VISIBILITY`, `SECTION_META` (+36 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **23 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `e()` connect `Chart.js Vendor Internals` to `Chart.js Core`, `._queueCallback()`, `Chart.js Scales & Drawing`, `loadLeads()`, `Dashboard UI Shell`, `Chart.js Elements & Updates`, `remove()`, `Dashboard API Client`, `qi`, `Bootstrap Carousel & Events`, `Chart.js Drawing Primitives`, `an()`, `bt`, `ra()`?**
  _High betweenness centrality (0.061) - this node is a cross-community bridge._
- **Why does `apiCall()` connect `Dashboard API Client` to `Chart.js Vendor Internals`, `loadLeads()`, `Dashboard UI Shell`, `initApp()`, `verifyCode()`, `executeBulkScraping()`, `loadDashboard()`, `loadProperties()`?**
  _High betweenness centrality (0.030) - this node is a cross-community bridge._
- **Why does `s()` connect `Chart.js Elements & Updates` to `Chart.js Vendor Internals`, `Chart.js Core`, `Chart.js Scales & Drawing`, `Bootstrap Vendor Bundle`, `Dashboard UI Shell`, `Chart.js Datasets`, `Chart.js Ticks & Scales`, `Bootstrap Components`, `Chart.js Helpers`, `Chart.js Drawing Primitives`, `a()`, `an()`, `bt`, `tn`, `we()`, `eo()`, `remove()`, `cn`, `.notifyPlugins()`, `ra()`, `._createDescriptors()`?**
  _High betweenness centrality (0.029) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `an()` (e.g. with `.hide()` and `.reset()`) actually correct?**
  _`an()` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 27 inferred relationships involving `n()` (e.g. with `._maybeEnableSmoothScroll()` and `ai()`) actually correct?**
  _`n()` has 27 INFERRED edges - model-reasoned connections that need verification._
- **What connects `SorinFlow Divar Scraper - API Package`, `SorinFlow Divar Scraper - API Routes`, `SorinFlow Divar Scraper - Authentication API Routes` to the rest of the system?**
  _312 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Chart.js Vendor Internals` be split into smaller, more focused modules?**
  _Cohesion score 0.045692883895131084 - nodes in this community are weakly interconnected._