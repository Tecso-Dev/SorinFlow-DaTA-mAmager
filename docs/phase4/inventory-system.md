# SorinFlow — Next.js migration checklist

Scope: پیامک (sms), ایمیل (email), هوش مصنوعی (ai), پایش سامانه (monitoring),
کاربران و بکاپ (users/backup/DR/maintenance/site-settings), رویدادها (audit),
the user's own profile page, and the admin's «درخواست‌های مشتریان» (portal
requests). Source: old panel `frontend/index.html` + `frontend/js/app.js`,
backend `app/api/routes/*.py`. All paths below are relative to
`/home/user/SorinFlow-DaTA-mAmager`. All endpoints are mounted under `/api`
(see `app/api/routes/__init__.py`) — e.g. `sms.router` → `/api/sms/*`.

Auth note for the rebuild: the old panel used a bearer token in
localStorage; the new Next.js panel (`frontend-next`) already uses the
cookie-based `/api/session/*` flow (`app/api/routes/session.py`) plus a
`X-CSRF-Token` header read from the `sf_csrf` cookie
(`frontend-next/src/lib/api.ts`). Every POST/PUT/PATCH/DELETE endpoint
listed below needs that CSRF header when called from the new panel; GETs
don't.

Role/permission model: sidebar visibility in the old panel is driven by
`NAV_PERMISSION` (permission-key gated, enforced server-side by
`require_permission(...)` dependencies on the router) and `NAV_ROLE_ONLY`
(hard role gate for `users`, `ai`, `audit` — root/super_admin only)
(`frontend/js/app.js:1309-1355`). `frontend-next/src/components/panel/nav.ts`
already has stub nav entries for every section below (`step: 7` or `8`,
i.e. not yet built) with the same perm/role gates — reuse that gating, it
matches the backend exactly.

---

## 1. پیامک — SMS (`nav-link-sms`, permission `sms`)

Old panel: `frontend/index.html:3212-3448` (section HTML),
`frontend/js/app.js:12636-12980` (all JS: `loadSms`, `loadSmsSettings`,
`saveSmsSettings`, `sendSmsTest`, `sendSingleSms`, `loadSmsAudiences`,
`pickAudience`, `sendBroadcast`, `smsPage`, `loadSmsMessages`,
`refreshSmsDelivery`, `loadSmsEvents`, `loadSmsCredit`, `loadSmsStats`).
Backend: `app/api/routes/sms.py` (full file read).

### Cards / widgets
1. **Stat row** (4 stat-cards): اعتبار باقی‌مانده (`GET /sms/account`),
   ارسال‌شده در ۳۰ روز / رسیده به گیرنده / ناموفق در ۳۰ روز
   (`GET /sms/stats`).
2. **تنظیمات کاوه‌نگار** card — `id="sms-settings-card"`, hidden
   (`d-none`) unless `_currentUser.role` is `root`/`super_admin` (client
   check only — server also enforces via `_super_admin` dep on
   `/sms/settings`). Fields: API key (write-only, masked on read), sender
   number, OTP template name, signature (appended to every send, max 60
   chars), enabled switch. "ارسال پیام آزمایشی" test-send box.
3. **رویدادهای سرویس پیامک** card — service-level event log (distinct
   from send history), filterable by stage (send/test/settings/template/
   error), auto-excludes `inbound` stage unless explicitly filtered.
4. **ارسال پیام تکی** card — single send form (to + body, live char/SMS-part
   counter using 70-char Persian billing rule).
5. **ارسال گروهی** card — broadcast, super_admin-only badge shown but not
   enforced client-side beyond disabling the button; audience picked from
   named groups (`staff`, `visitors`, `marketing`, `contacts`, `requests`),
   count shown before sending, campaign name optional, confirm dialog,
   button re-labelled with the live recipient count.
6. **تاریخچهٔ ارسال** — paginated (50/page) table with search (number or
   text) + status filter, "بروزرسانی وضعیت" button to pull delivery status
   from Kavenegar for non-final messages.

### Endpoints (`app/api/routes/sms.py`)
| Method | Path | Params/Body | Response | Role |
|---|---|---|---|---|
| GET | `/sms/settings` | — | `{key_source, api_key_masked, configured, sender, otp_template, signature, enabled, provider}` | root/super_admin |
| PUT | `/sms/settings` | body `SmsSettingsIn{api_key?, sender?, otp_template?, signature?, enabled?}` | same as GET | root/super_admin |
| GET | `/sms/account` | — | `{ok, remaining_credit, expire_date, ...}` or `{ok:false, status, error}` | any staff (perm `sms`) |
| POST | `/sms/test` | query `to` | `{ok, via, message_id, cost}` or `{ok:false, via, error}` | root/super_admin |
| POST | `/sms/send` | body `SendIn{to, message}` | `{ok, message_id}` or `{ok:false, error}` | any staff |
| GET | `/sms/audiences` | — | `{audiences:[{key,label,count}]}` | any staff |
| POST | `/sms/broadcast` | body `BroadcastIn{audience? , numbers?, message, campaign?, confirm_count}` | `{ok, campaign, sent, failed, total, error}`; 409 if `confirm_count` mismatches live count | root/super_admin |
| GET | `/sms/messages` | query `limit(≤200), offset, campaign?, status?, search?` | `{total, items:[SmsLog]}` | any staff |
| GET | `/sms/campaigns` | — | `{campaigns:[{campaign,total,cost,at}]}` (top 40) | any staff |
| POST | `/sms/messages/refresh-status` | query `limit(≤200), campaign?` | `{ok, checked, updated}` | any staff |
| GET | `/sms/events` | query `limit(1-500), stage?, level?` | `{events:[...], count}` | any staff |
| GET | `/sms/stats` | — | `{total, last_30_days, failed_30_days, delivered_30_days, cost_30_days, success_rate}` | any staff |

### Role/permission
Router-level: `dependencies=_perm("sms")` in `app/api/routes/__init__.py:64`
→ every `/sms/*` route requires the `sms` permission just to reach the
router; settings/test/broadcast additionally require `root`/`super_admin`
inside the route (`_super_admin = Depends(_role_dep(ROLE_ROOT, ROLE_SUPER_ADMIN))`).

### Polling / special UX
- No auto-poll; everything loads once on `showSection('sms')` via `loadSms()`.
- API key is **write-only** — never returned in full, only masked
  (`sf12****ab34`); an empty string in the PUT body means "clear the key".
- `sms-key-source` shows whether the key came from `env` (deploy-time) or
  `panel` (DB) — env wins if both are set.
- Broadcast requires a **confirm dialog** + server-side `confirm_count`
  check (optimistic-lock style: if the live audience size changed since
  the count was shown, server 409s and the panel must reload counts).
- Iranian mobile normalization (`09xx`/`9xx`/`+989xx` → `09xxxxxxxxx`)
  happens both client hint and server (`normalize_mobile` in `sms.py`).
- Test-send picks OTP-template route (`send_verify`, no sender line
  needed) over plain `send_sms` when a template is configured — UI shows
  which route it used ("از راه …").

---

## 2. ایمیل — Email (`nav-link-email`, permission `email`)

Old panel: `frontend/index.html:3449-3717`,
`frontend/js/app.js:13175-13525` (`loadEmail`, `loadEmailAudiences`,
`emPreview`/`emPreviewSoon`, `sendEmailBroadcast`, `exportEmailAudience`,
`loadEmailStats`, `loadEmailSettings`, `saveEmailSettings`,
`verifyEmailSmtp`, `sendEmailTest`, `sendOneEmail`, `loadEmailTemplates`,
`previewEmailTemplate`, `loadEmailMessages`).
Backend: `app/api/routes/email.py` (full file read).

### Cards / widgets
1. **Stat row**: وضعیت اتصال (`em-state`, from settings' `configured`/host
   reachability), ارسال‌شده در ۳۰ روز, کد ورود ارسالی (`login_code`
   template count), ناموفق در ۳۰ روز — all from `GET /email/stats`.
2. **تنظیمات ایمیل (SMTP)** card, super_admin-only (hidden client-side,
   enforced server-side). Fields: host, port, username, password
   (write-only masked), security (`starttls`/`ssl`/`none`), from-name,
   from-email (optional override, must be a Gmail "Send mail as" verified
   address), reply-to, enabled switch. App-password hint banner for Gmail
   (shown/hidden based on host). Buttons: ذخیرهٔ تنظیمات, بررسی اتصال
   (`POST /email/verify` — logs in and hangs up, sends nothing), ارسال
   ایمیل آزمایشی (`POST /email/test`).
3. **ارسال ایمیل** card (super_admin only via `crm-superadmin-only` class)
   — free-form to/subject/body send through the "notification" template.
4. **پیش‌نمایش قالب‌ها** — a `<select>` of template keys
   (`GET /email/templates`) rendered into an `<iframe>` via
   `GET /email/preview/{name}` (HTML response, fetched with auth header
   then set as `srcdoc` — **not** pointed at with `src` directly, since the
   route needs the bearer/session).
5. **کمپین ایمیلی** card (super_admin only) — named audience picker
   (`marketing`, `visitors`, `staff`, `contacts`), subject/body/CTA
   label+URL fields, live debounced preview (`POST /email/broadcast/preview`
   → HTML into iframe, 400ms debounce via `emPreviewSoon`/`_emPreviewTimer`),
   confirm-count guarded broadcast send.
6. **تاریخچهٔ ارسال** table — filter by template (`broadcast`/`login_code`/
   `notification`) and status.

### Endpoints (`app/api/routes/email.py`)
| Method | Path | Params/Body | Response | Role |
|---|---|---|---|---|
| GET | `/email/settings` | — | `{host, port, user, password_masked, configured, password_source, from_name, reply_to, from_email, security, enabled}` | super_admin |
| PUT | `/email/settings` | body `EmailSettingsIn` (all optional) | same shape | super_admin |
| POST | `/email/verify` | — | connection check result (no send) | super_admin |
| POST | `/email/test` | query `to` | `{ok, message_id}` / `{ok:false, error, detail}` | super_admin |
| POST | `/email/send` | body `SendIn{to, subject, message, cta_label?, cta_url?}` | `{ok, message_id}` / `{ok:false, error}` | super_admin |
| GET | `/email/templates` | — | `{templates:[{key,label}]}` | any staff |
| GET | `/email/preview/{name}` | — | raw HTML (iframe) | any staff |
| GET | `/email/audiences` | — | `{audiences:[{key,label,count}]}` | any staff |
| POST | `/email/broadcast` | body `BroadcastIn{audience, subject, message, cta_label?, cta_url?, confirm_count}` | `{ok, sent, failed, total}`; 409 on count mismatch | super_admin |
| POST | `/email/broadcast/preview` | body `BroadcastPreviewIn` | raw HTML | super_admin |
| GET | `/email/export` | query `audience` (default `marketing`) | `{audience, label, count, emails:[...]}` — **PII export** | super_admin |
| GET | `/email/messages` | query `limit(≤200), offset, template?, status?` | `{total, items:[EmailLog]}` | any staff |
| GET | `/email/stats` | — | `{total, last_30_days, failed_30_days, login_codes_30_days, success_rate}` | any staff |

### Role/permission
Router-level `_perm("email")` gate; settings/verify/test/send/broadcast/
export all additionally require `_super_admin` inside the route.

### Polling / special UX
- No auto-poll.
- `POST /email/verify` is deliberately separate from `/email/test` — one
  answers "is the password right" (no email sent), the other proves the
  whole pipeline end-to-end with a styled message.
- Audiences only include **verified** addresses (`email_verified == True`);
  `marketing` additionally requires `marketing_opt_in`.
- `GET /email/export` returns raw email addresses — treat as sensitive PII
  in the new UI (no caching, no accidental console.log).
- Broadcast preview renders through the exact same template function the
  send uses (`email_templates.notification`), so what's shown is what
  ships.

---

## 3. هوش مصنوعی — AI (`nav-link-ai`, root/super_admin only, no permission key)

Old panel: `frontend/index.html:2728-2900`,
`frontend/js/app.js:4345-4730` (`loadAi`, `aiSave`, `aiTest`,
`loadAiScreen`, `aiAgentToggle`, `aiAgentCapEdit`, `aiRunAgent`,
`aiShowErrors`, `loadAiLog`, `loadAiChats`, `_aiAssistantStatus`,
`aiAssistantToggle`, `aiAssistantAsk`, `aiAssistantLog`, `aiUsage`).
Backend: `app/api/routes/ai.py` + `app/api/routes/ai_assistant.py` (full
files read). Per-agent control routers also exist but were out of this
scope's primary read: `ai_reader.py` (`/ai/reader/status`, `/ai/reader/run`),
`ai_embed.py` (`/ai/embed/status`, `/ai/embed/run`), `ai_photo.py`
(`/ai/photo/status`, `/ai/photo/run`) — all root/super_admin, called from
the agent grid's "اجرای یک دور" button.

### Cards / widgets
1. **Tile row** (`ai-tiles`): اتصال, هزینهٔ امروز (+ cap), این ماه, توکن
   رایگان لیارا امروز — from `GET /ai/status` (or `/ai/overview`, which is
   the richer combined call `loadAiScreen()` uses).
2. **ایجنت‌ها** grid (`ai-agents-grid`) — one card per agent (`explainer`,
   `reader`, `need`, `embed`, `vision`, `assistant`), each with: on/off
   toggle (`PUT /ai/agents/{key}`), its own daily cap
   (`PUT /ai/agents/{key}/cap`), today/month calls+cost+failures, an
   "اجرای یک دور" manual-run button for `reader`/`embed`/`vision`
   (`POST /ai/reader/run|/ai/embed/run|/ai/photo/run`), and an error chip
   that jumps the log below to that agent's failures
   (`aiShowErrors`).
3. **لاگ فراخوانی‌ها** — `GET /ai/log` filterable by agent + "فقط خطاها"
   checkbox.
4. **سؤال‌های دستیار** — chat-style transcript of "سورین" Q&A
   (`GET /ai/assistant/log`), with a "بپرس" button to ask from the panel
   itself (`POST /ai/assistant/ask`).
5. **هوش مصنوعی** settings card (`ai-card`) — connection/cost mini-stats,
   4 model fields (write/read/vision/embed — free text, env fallback if
   blank), daily cap ($, 0-100), office notes (appended to customer-facing
   text, max 600 chars), master enabled switch, «سورین» assistant sub-card
   (its own enabled switch + بپرس + سؤال‌ها buttons), Save / تست اتصال /
   آخرین مصرف‌ها buttons.

### Endpoints (`app/api/routes/ai.py`, `/ai` prefix)
| Method | Path | Params/Body | Response | Role |
|---|---|---|---|---|
| GET | `/ai/status` | — | connection/models/usage/breaker/liara + static agent list | root/super_admin |
| GET | `/ai/overview` | — | full screen payload: cfg + usage + per-agent `{enabled, model, state, cap_usd, month, today}` | root/super_admin |
| PUT | `/ai/agents/{key}` | body `{enabled}` | `{key, enabled}`; 404 unknown key | root/super_admin |
| PUT | `/ai/agents/{key}/cap` | body `{cap_usd: 0-100}` | `{key, cap_usd}` | root/super_admin |
| GET | `/ai/log` | query `agent?, failed_only?, limit(1-300)` | `{items:[AiUsage], agents:[...]}` | root/super_admin |
| PUT | `/ai/settings` | body `AiSettingsIn{enabled?, daily_cap_usd?, notes?, model_write?, model_read?, model_vision?, model_embed?}` | `llm.config(db)` | root/super_admin |
| POST | `/ai/test` | — | `{model, ms, ...}`; 502 on `LLMError` — ignores the daily cap deliberately | root/super_admin |
| GET | `/ai/usage` | query `limit(1-200)` | `{items:[AiUsage], summary}` | root/super_admin |

### Endpoints (`app/api/routes/ai_assistant.py`, `/ai/assistant` prefix)
| Method | Path | Body | Response | Role |
|---|---|---|---|---|
| GET | `/ai/assistant/status` | — | `{enabled, telegram_configured, configured, linked_users, questions_today, last, ...}` | root/super_admin |
| PUT | `/ai/assistant/settings` | `{enabled}` | status payload | root/super_admin |
| POST | `/ai/assistant/ask` | `{text}` (max `assistant.MAX_QUESTION`) | `{ok, text, ms, tools}`; 502 if it fails | root/super_admin |
| GET | `/ai/assistant/log` | query `limit(1-200)` | `{items:[AiChat]}` | root/super_admin |

### Role/permission
Both routers are mounted **without** a permission dependency
(`router.include_router(ai.router, prefix="/ai", ...)` — no `_perm(...)`);
every route inside checks `_role_dep("root", "super_admin")` itself. Nav
gate matches (`NAV_ROLE_ONLY['nav-link-ai']`).

### Polling / special UX
- No auto-poll; manual refresh buttons only (`loadAiScreen()`).
- Model fields are **free text** (not a dropdown) — env value is the
  fallback shown as a placeholder/hint when the field is empty
  (`env_models()`); the key and base URL themselves are never editable
  here — they come from GitHub-managed env vars only (see SECRETS.md §2d).
- Per-agent daily cap sits **inside** the shared daily cap — hitting either
  pauses that agent (or all of them) until Tehran midnight.
- The "circuit breaker" (`breaker_status()`) shows a pause state after
  repeated upstream failures — surface `_aiBreakerText` equivalent.
- "بپرس" from the panel uses the exact same assistant pipeline as
  Telegram, logged the same way, with the panel user as asker.

---

## 4. پایش سامانه — Monitoring (`nav-link-monitoring`, permission `monitoring`)

Old panel: `frontend/index.html:2901-3211`,
`frontend/js/app.js:12061-12377` (client-errors, overview render, GCP)
and `frontend/js/app.js:12378-12571` (live polling + chart) and
`frontend/js/app.js:12267-12377` (runtime card) and
`frontend/js/app.js:13051-13150` (Divar session/cookie health).
Backend: `app/api/routes/monitoring.py` (full file read), plus
`app/api/routes/gcp.py` and `app/api/routes/stats.py` (`/stats/logs`).

### Cards / widgets
1. **Health tile row**: پایگاه داده + ms, Redis + ms, فضای اشغال‌شدهٔ دیسک
   (+ progress bar, amber >75%/red >90%), مدت کارکرد + حافظه — from
   `GET /monitoring/overview`.
2. **سرور و سیستم** table: OS/distro (container's, explicitly not the
   host's — labelled), arch+Python, hostname, server IP, domain.
3. **مدت کارکرد و اتصال** table: panel process uptime, host uptime,
   scraper uptime/last-completed, Divar reachability (cached 60s) + «تست
   واقعی» button → `POST /monitoring/connectivity-test` (4-stage DNS/TCP/
   TLS/HTTP probe against a **fixed** target table, never an arbitrary
   host — anti-SSRF by design).
4. **وضعیت نشست‌های دیوار** table — `GET /monitoring/cookies`, scoped to
   the caller's own sessions (root sees the pool). Per-row "بررسی واقعی"
   button → `POST /monitoring/cookies/check?phone=`, a real outbound probe
   against Divar's own API, updates `is_valid`/expiry.
5. **وضعیت تسک‌های اسکرپر** + **Google Cloud** cards (jobs-by-status
   badges, stale-running warning >6h; GCP state badge + test button
   `POST /gcp/test`, `GET /gcp/status`).
6. **پردازه‌ها و کارهای پس‌زمینه** card (`mon-runtime-card`) — hidden
   unless role is root/super_admin (client check; server enforces via
   `require_super_admin` on `GET /monitoring/runtime`). Shows every
   process's heartbeat + sandbox info, every supervised loop's health
   (stale/off/restarted), the scrape queue length and in-flight job ids.
7. **خطاهای مرورگر کاربران** — `GET /monitoring/client-errors?limit=60`,
   clear button `DELETE /monitoring/client-errors`.
8. **وضعیت زندهٔ سرور** — live chart (Chart.js) + CPU/RAM/Swap bars,
   polling `GET /monitoring/live` every 5s (`tickLive`, rates derived
   client-side from two consecutive counter samples — no server-side
   history), with a توقف/ادامه toggle (`toggleLive`).
9. **لاگ سامانه** viewer — level filter (ERROR/WARNING/INFO) + free-text
   grep, reads `GET /stats/logs` (note: **not** under `/monitoring` —
   lives in `stats.py`), `log` param picks `scraper.log`/`api.log`/
   `scheduler.log`, tails up to 1000 lines server-side in a thread pool.

### Endpoints (`app/api/routes/monitoring.py`, `/monitoring` prefix)
| Method | Path | Params | Response | Role |
|---|---|---|---|---|
| POST | `/monitoring/connectivity-test` | query `target` (fixed key, default `divar`) | `{host, port, ip, stages:[{stage,ok,ms,detail}], rtt_ms, ok, total_ms, target}` | any staff (perm `monitoring`) |
| GET | `/monitoring/client-errors` | query `limit` (≤60) | `{items}` | any staff |
| DELETE | `/monitoring/client-errors` | — | `{success}` | any staff |
| GET | `/monitoring/live` | — | cheap snapshot: counters, cpu/mem/swap, disk % (poll every 5s) | any staff |
| GET | `/monitoring/overview` | — | full health/resources/throughput payload (poll every 30s while section open) | any staff |
| GET | `/monitoring/cookies` | — | `{items:[...], total, usable, rotation_possible, rotate_every, check_every_minutes, stale_after_minutes}` | any staff |
| POST | `/monitoring/cookies/check` | query `phone` | live check result; 404 if not caller's own session | any staff |
| GET | `/monitoring/runtime` | — (`dependencies=[require_super_admin]`) | `{processes, loops, queue_length, running}` | root/super_admin |

### Endpoints used on this screen from other modules
| Method | Path | Notes |
|---|---|---|
| GET | `/gcp/status` | admin (`require_admin`) |
| POST | `/gcp/test` | super_admin |
| GET | `/gcp/services` | query `minutes(5-1440)` |
| GET | `/stats/logs` | admin only; query `lines(1-1000), grep, level, log` |

### Role/permission
Router-level `_perm("monitoring")` for the whole `/monitoring` router
(`app/api/routes/__init__.py:62`) and `_perm("monitoring")` for `/gcp` too
(`:63`) — both share the same permission key. `/monitoring/runtime` has
an extra hard role gate on top. `/stats/logs` uses a separate
`_require_admin` dependency (any admin/super_admin/root, not gated by the
`monitoring` permission key — worth confirming this doesn't drift when
rebuilt).

### Polling / special UX
- **Two different poll loops**: overview refresh every 30s
  (`_monTimer`, self-cancels once the section isn't visible any more —
  replicate with an effect cleanup / visibility check in React, don't
  poll a hidden route), live snapshot every 5s (`_liveTimer`).
- Connectivity probe target list is a **fixed dict** server-side
  (`_PROBE_TARGETS = {"divar": ("divar.ir", 443)}`) — never accept an
  arbitrary host from the client; this is explicitly anti-SSRF.
- CPU/mem numbers come from cgroup v1 or v2 (best-effort, `None` where
  unavailable — e.g. local dev) — the UI must tolerate missing fields
  rather than showing `0%`.
- `mon-runtime-card` visibility must be re-checked on every load
  (`loadRuntime` toggles `d-none` itself based on `_currentUser.role`).

---

## 5. کاربران و بکاپ — Users + Backup + DR + Maintenance (`nav-users`, root/super_admin only)

Old panel: `frontend/index.html:3751-4047` (whole `section-users`, five
cards: tickets, maintenance, backup, DR, users table) plus the create-user
modal at `frontend/index.html:4531-4577` and the perms-editor modal
(`permsEditorModal`, referenced from `frontend/js/app.js:9129+`).
JS: `frontend/js/app.js:8960-9300` (users CRUD/table),
`frontend/js/app.js:11793-11890` (upgrade tickets),
`frontend/js/app.js:4270-4345` + `:5090-5160` (maintenance),
`frontend/js/app.js:4740-5090` (backup + DR).
Backend: `app/api/routes/users.py` (full file), `app/api/routes/backup.py`
(full file), and maintenance endpoints registered directly on `app` in
`app/main.py:1128-1201` (**not** under `app/api/routes/` — they live at
`/api/maintenance`, not `/api/users/maintenance` or similar).

### 5a. درخواست‌های دسترسی به پنل (upgrade tickets)
Card lists pending `UpgradeTicket`s (visitors asking to become admin), a
per-user permission-checkbox box, تأیید/رد buttons.
| Method | Path | Body | Response | Role |
|---|---|---|---|---|
| GET | `/portal/admin/tickets` | query `status?` | `{items:[{...ticket, user:{id,full_name,phone,email}}], total}` | super_admin |
| POST | `/portal/admin/tickets/{id}/decide` | `UpgradeTicketDecision{approve, permissions?, decision_note?}` | ticket dict; approve raises role to `admin` with chosen perms (defaults to `DEFAULT_ADMIN_PERMISSIONS` if none picked); rejects clear `granted_permissions`. Audits `portal_ticket_decide`. Emails the decision. | super_admin |

### 5b. حالت تعمیر سایت (maintenance mode)
Fields: message text, duration dropdown (1h/6h/1d/3d/1w/no-timer), emergency
phone, support email. Buttons: بستن سایت / باز کردن سایت / ذخیرهٔ تنظیمات
(save-only path re-applies current settings without a full toggle+confirm,
only usable while already closed). A countdown (`maintenance-eta`) and a
bypass-link box (`maintenance-bypass`, shown after closing — lets the admin
whitelist a second device without logging in there first) are rendered from
the response.
| Method | Path | Body | Response | Role |
|---|---|---|---|---|
| GET | `/api/maintenance` | — | `{enabled, message, until, seconds_left, contact_phone, contact_email, bypass_active, ...}` — **public**, needed by the login page | none (public) |
| POST | `/api/maintenance` | `{enabled, message?, hours?, until?, contact_phone?, contact_email?}` | same shape + `bypass_url` (only when `enabled`); sets an httpOnly bypass cookie | super_admin (`_require_super_admin` dep in `main.py`) |

Note: these two routes are **not** under `/api/maintenance` via the normal
router include — they're defined straight on `app` in `main.py`. When
rebuilding, double-check the exact path is `/api/maintenance` (GET+POST,
same path, different methods) and that it must stay reachable **before**
the maintenance-mode middleware itself blocks requests (see
`main.py:548-560`, the middleware explicitly whitelists `/api/maintenance`).

### 5c. بکاپ و نسخهٔ خارج از سرور (nightly DB snapshot → Telegram)
Card shows last-local-snapshot / last-offsite-send stats, Telegram bot
token (write-only, masked) + chat-id(s) (comma-separated, "پیدا کن" probes
the bot for chats that have messaged it), and a 3-way route picker (manual
proxy / dashboard proxy pool (rotating) / Cloudflare relay Worker — with a
"کد Worker" link that fetches the actual deployed worker source so the
panel never shows drifted instructions). تست این راه button, ذخیره, همین
حالا بکاپ بگیر و بفرست, and a «خلاصهٔ صبحگاهی» (morning digest) sub-card
with پیش‌نمایش و ارسال الان.
| Method | Path | Body | Response | Role |
|---|---|---|---|---|
| GET | `/backup/status` | — | configured/token_masked/chat_ids/route info/snapshots(top5)/snapshot_count/last_offsite/digest_hour/digest_last_sent | super_admin |
| PUT | `/backup/settings` | `BackupSettingsIn{bot_token?, chat_id?, proxy_mode?, proxy?, proxy_pool?, relay?, relay_key?}` | `backup_status()` | super_admin |
| POST | `/backup/proxy-test` | `ProbeIn` (route override for testing before saving) | `{bot, ms, proxy, results?}` (per-proxy results when mode=`pool`) | super_admin |
| POST | `/backup/probe` | `ProbeIn` | `{bot, chats:[{id,...}], hint_fa?}` — chats that have messaged the bot | super_admin |
| GET | `/backup/digest` | — | `{text, counts, hour, last_sent, configured}` | super_admin |
| POST | `/backup/digest/send` | — | send result; 502 if it fails | super_admin |
| POST | `/backup/run` | — | `{file, telegram_sent, last_offsite, ...}`; audits `backup_run` | super_admin |
| POST | `/backup/diagnose` | — | `{rows:[...]}` — tests every route (direct/relay/each proxy) at once | super_admin |
| GET | `/backup/relay-worker` | — | plaintext `worker.js` source (read live from `deploy/telegram-relay/worker.js`) | super_admin |

### 5d. بکاپ کامل — DR (disaster recovery)
Host-level nightly (04:00 Tehran) full bundle via `scripts/dr_backup.sh`
(DB + secrets + Divar sessions + browser profiles + SSL). Card: last-run +
state, همین حالا (drops a request file the host's `dr-backup.path` systemd
unit watches — async, panel just asks), تست همهٔ راه‌ها (diagnose).
| Method | Path | Body | Response | Role |
|---|---|---|---|---|
| GET | `/backup/dr` | — | `{last_run, history(top5), last_alert, requested, undelivered, schedule_fa}` | super_admin |
| POST | `/backup/dr/run` | — | `{requested:true}`; 409 if a request is already pending; audits `dr_run` | super_admin |

(DR's own "diagnose" reuses `POST /backup/diagnose` per the JS — confirm
during rebuild whether DR has a distinct diagnose call or shares this one.)

### 5e. اعضای تیم (users table)
Full-width table: avatar+name+headline, role pill, permissions
(admin-only, opens `permsEditorModal`), contact (phone+email with
verified/unverified tick — **root only** gets an inline toggle switch to
manually flip verification, everyone else sees a read-only tick; a
"درخواست تأیید" nudge link appears for unverified contacts), state
(active/inactive), last login, kebab menu (نقش و دسترسی‌ها / شمارهٔ دیوار /
تغییر رمز / فعال-غیرفعال / حذف — root accounts can't be edited except by
root; nobody can deactivate/reset/delete themselves). Search + role filter
+ active/inactive filter, all client-side over one `GET /users` fetch.
New-user modal (`newUserModal`) and perms-editor modal (`permsEditorModal`,
role dropdown + permission checkboxes, syncs visibility of the perm box to
`role === 'admin'`).

| Method | Path | Body | Response | Role |
|---|---|---|---|---|
| GET | `/users` | — | `{items:[UserResponse], total}` (root sees root rows too; super_admin filtered) | super_admin |
| POST | `/users` | `UserCreate{username, full_name?, email?, password, role, divar_phone?, permissions?}` | `UserResponse`; 400 on dup username/email; audits `user_create` | super_admin |
| PATCH | `/users/{id}` | `UserUpdate{email?, full_name?, role?, is_active?, divar_phone?, phone?, permissions?}` | `UserResponse`; audits `user_update` (field **names** only, never values) | super_admin (root-only for touching a root row) |
| PATCH | `/users/{id}/verification` | `{phone_verified?, email_verified?}` | `UserResponse`; logged at WARNING; audits `user_verification_set` | **root only** |
| POST | `/users/{id}/password` | `{new_password}` | `{success, message}`; audits `user_password_reset_admin` | super_admin |
| DELETE | `/users/{id}` | — | `{success}`; can't delete self; audits `user_delete` | super_admin |
| POST | `/users/{id}/totp/disable` | — | force-disables a user's 2FA; audits `user_totp_disable_admin` | super_admin |
| POST | `/users/{id}/verification-request` | — | nudges the user by email/SMS to verify; rate-limited 1/hour/person | super_admin |
| GET | `/users/permissions/catalog` | — | `{items:[{key,label}], defaults}` | super_admin |

### 5f. Site / brand settings
**No backend endpoint exists.** There is no `/api/public/site` or similar
— confirmed by grepping `app/main.py` and `app/api/routes/*.py` for any
`/public/*` route beyond `/public/auth` (visitor sign-up, unrelated).
`AppSetting` (`app/models/app_setting.py`) is a generic key/value store
already used for maintenance mode, but nothing populates brand/office
name, domain, or contact details through it. `settings.domain` (env var
`DOMAIN`, default `scc.sorinflow.com`, `app/config.py:26`) is read-only
and only ever *displayed* (monitoring's «دامنه» tile;
`main.py:1510`'s `profile_url` builder). See §7 for every hardcoded
occurrence of the brand name / domain / contact address, and consider
whether the Next.js rebuild should add a real settings endpoint (e.g.
`GET/PUT /api/settings/site` gated to super_admin, backed by `AppSetting`)
rather than re-hardcoding the same strings in TSX.

### Role/permission (whole section)
Nav gate: `NAV_ROLE_ONLY['nav-users'] = ['root','super_admin']`
(client). Server-side every route above independently checks
root/super_admin (mostly via `_role_dep(ROLE_ROOT, "super_admin")` or the
`_require_super_admin`/`require_super_admin` dependency) — there is no
single umbrella permission key covering this whole page, unlike sms/email/
monitoring which are gated by one permission string on the router.

### Polling / special UX
- No auto-poll anywhere in this section; every card has its own manual
  refresh button.
- Maintenance's "save settings" (`saveMaintenanceSettings`) is a no-op
  guard when the site is currently open — it warns instead of calling the
  API, since settings only take effect at the next close.
- Backup's Telegram route picker has **3 mutually exclusive panes**
  (`bk-pane-manual` / `bk-pane-pool` / `bk-pane-relay`) toggled by
  `bkModeChanged()` — only one is sent to the server per save.
- The users table does **all filtering client-side** over a single `GET
  /users` call — no server-side search/pagination params exist today; the
  Next.js rebuild can keep doing this or add real query params (there
  currently are none to reuse).
- Editing a `root` account's role in the perms editor **must preselect the
  actual current role** (a real regression happened here: a blank fallback
  to `admin` silently demoted a root account on save — see the comment in
  `openPermsEditor`, `frontend/js/app.js:9129-9180`). Preserve that
  behaviour exactly.

---

## 6. رویدادها — Audit (`nav-link-audit`, root/super_admin only)

Old panel: `frontend/index.html:4050-4085`,
`frontend/js/app.js:11897-11985` (`loadAuditActions`, `loadAuditEvents`,
`_renderAuditPagination`, `goToAuditPage`, `_auditFilterChanged`).
Backend: `app/api/routes/audit.py` (full file read).

### Cards / widgets
Single card: filter row (actor search text, action `<select>` populated
from `GET /audit/actions`, since/until date inputs in Jalali format
converted client-side via `jalaliToGregorian`), table (زمان, کاربر,
عملکرد, هدف, IP), pagination (50/page, numbered with ellipsis).

### Endpoints
| Method | Path | Params | Response | Role |
|---|---|---|---|---|
| GET | `/audit/events` | `actor?, action?, since?(ISO or bare date), until?, limit(1-200), offset` | `{items:[{id, created_at, actor_user_id, actor_username, actor_role, action, action_label, target_type, target_id, summary, detail, ip, request_id}], total}` | root/super_admin |
| GET | `/audit/actions` | — | `{items:[{key,label}]}` — known action keys, Persian labels | root/super_admin |

### Role/permission
Router mounted with **no** permission dependency; both routes check
`_role_dep(ROLE_ROOT, ROLE_SUPER_ADMIN)` internally.

### Polling / special UX
- No auto-poll, manual refresh only.
- `until` handling: a **bare date** (`len == 10`, i.e. `YYYY-MM-DD`) is
  treated as "through the end of that day" (`+timedelta(days=1)`); a full
  timestamp is used as-is. Preserve this in the date-range filter.
- `actor` filter matches the **snapshot username** stored on the row at
  write time (`actor_username`), not a live join to `users` — a later
  rename doesn't change what an old row says. Don't "improve" this into a
  join during the rebuild without checking that's intended.
- `detail` is a free-form JSON blob per action; render defensively (it's
  written by many different call sites with different shapes).

---

## 7. Profile page (own profile — no nav item; opened via the sidebar
user-card / avatar, all authenticated staff)

Old panel: `frontend/index.html:1228-1456` (whole `section-profile`) +
`frontend/index.html:5202+` (`twoFAModal`).
JS: `frontend/js/app.js:654-772` (2FA modal), `:772-1000` (`loadProfile`,
`pfRender`, `pfSaveProfile`, `pfSetPresence`, `pfUploadAvatar`,
`pfRemoveAvatar`, `pfChangePassword`, `pfTelegramCode`/`pfTelegramUnlink`),
`:1000-1090` (Divar-account list), `:1099-1235` (verification nudges,
phone verify flow, email-2fa toggle).
Backend: `app/api/routes/users.py` (`/me/*` routes — full file already
read above), `app/api/routes/telegram_link.py` (full file read).

### Cards / widgets
1. **Hero card**: avatar (upload/remove, 400×400 JPEG center-crop, ≤5MB,
   EXIF-stripped server-side), name, headline, role pill, username,
   member-since, last login, **«IP شما»** (server's view of the caller's
   address — `GET /users/me/ip`, used to sanity-check per-IP rate limits
   see real callers behind the ingress), presence `<select>`
   (available/busy/away, `PATCH /users/me` with `{presence}`).
2. **مشخصات** form: full name, username (renaming re-issues the JWT/
   session since it names the token subject — `reissue()` in the PATCH
   response), headline, bio, website/instagram/linkedin links (validated
   & normalized server-side — instagram handle → full URL).
3. **اتصال به تلگرام (دستیار سورین)** card: link status, «دریافت کد
   اتصال» (mints a 10-min one-time code + deep link
   `https://t.me/{bot}?start={code}`, polls every second client-side for
   up to the code's TTL checking if the link completed), «قطع اتصال».
4. **تماس و تأیید** card:
   - Email: current value + verified badge, change flow (type new email →
     send code → confirm code; the **old** address keeps working until the
     new one is confirmed).
   - Phone: current value + verified badge, same change-then-verify-by-SMS
     pattern; a code that arrives by any channel other than SMS is
     explicitly rejected as proof of phone ownership.
   - **حساب‌های دیوار من**: every Divar session (`Cookie` row) this user
     owns, primary-account badge, «پیش‌فرض» / حذف actions per row,
     «افزودن شماره» jumps to the «احراز هویت دیوار» section (adding IS
     logging in with a new number there).
5. **امنیت** card: change-password form (invalidates every other
   session/device via `token_version` bump; this device gets a fresh
   token), TOTP/email-2FA settings opened via a separate modal
   (`twoFAModal`).

### `twoFAModal` (2FA setup)
- Status badge (`GET /users/me/totp/status`).
- Email-2FA toggle (`loadEmail2faState`/`toggleEmail2fa`, separate from
  TOTP — see role note below) — calls `POST /users/me/email-2fa`.
- TOTP setup: `POST /users/me/totp/setup` (idempotent — returns the
  existing secret if already generated) → QR code (`qrcode.min.js`) +
  manual secret, 6-digit confirm → `POST /users/me/totp/enable`.
- TOTP disable: re-enter password → `POST /users/me/totp/disable`.

### Endpoints (`app/api/routes/users.py`, `/users` prefix, all `/me/*`)
| Method | Path | Body | Response | Notes |
|---|---|---|---|---|
| GET | `/users/me` | — | `UserResponse` (permissions resolved to the effective list) | any authenticated staff |
| PATCH | `/users/me` | `ProfileUpdate{username?, full_name?, headline?, bio?, presence?, links?}` | `{user, access_token?}` — token reissued only if username changed | — |
| POST | `/users/me/password` | `PasswordChangeRequest{current_password, new_password}` | `{success, message, access_token}`; bumps `token_version` (signs out other devices); rate-limited like login | — |
| POST | `/users/me/email/request` | `EmailChangeRequest{email?}` | `{sent, verified, email(masked), message}` | omit `email` to re-verify the current one |
| POST | `/users/me/email/verify` | `{code}` | `{verified, email, message}` | |
| POST | `/users/me/email-2fa` | `{enabled}` | `{enabled, email(masked), message}`; 400 if no email on file | |
| GET | `/users/me/phone-gate` | — | `{required, message, phone, phone_verified}` — whether phone-gated actions are blocked | used to auto-open the verify popup on arrival |
| POST | `/users/me/phone/request` | `PhoneChangeRequest{phone?}` | `{sent, verified, phone, message}` | SMS-only; number only updates once the code is issued |
| POST | `/users/me/phone/verify` | `{code}` | `{verified, message}`; 400 if the code didn't arrive via SMS | |
| PATCH | `/users/me/divar-phone` | `{divar_phone}` | `UserResponse` | sets the "primary" Divar number; refuses another user's number |
| POST | `/users/me/avatar` | multipart `file` | `{avatar_url}` | ≤5MB, decoded+recompressed, random token filename |
| DELETE | `/users/me/avatar` | — | `{avatar_url:null}` | |
| GET | `/users/me/totp/status` | — | `{enabled}` | |
| POST | `/users/me/totp/setup` | — | `{secret, qr_uri, enabled}` | |
| POST | `/users/me/totp/enable` | `{code}` | `{success, message}` | spends the TOTP step so it can't double as the next login's code |
| POST | `/users/me/totp/disable` | `{password}` | `{success, message}` | rate-limited like a login attempt |

### Endpoints used on this page from other modules
| Method | Path | Notes |
|---|---|---|
| GET | `/users/me/ip` | «IP شما» |
| GET | `/auth/cookies?mine=1` | Divar accounts list (from `auth.py`, out of this scope's routes but consumed here) |
| DELETE | `/auth/cookies/{id}` | remove a Divar session |
| GET | `/users/me/telegram` | `app/api/routes/telegram_link.py` — `{linked, ...}` or `{linked:false}`; a link survives only until the next password change/sign-out-everywhere (`token_version` must match) |
| POST | `/users/me/telegram/link-code` | `{code, expires_in, command, deep_link}` |
| DELETE | `/users/me/telegram` | unlink; audits `telegram_unlink` |

### Role/permission
No permission gate — every authenticated **staff** account (not visitors)
has a profile page; `telegram_link.router` is mounted with
`dependencies=[Depends(get_staff_user)]` specifically to exclude visitors.
`PATCH /users/{id}/verification` (the **admin-side** manual-verify toggle
seen in the users table, §5e) is root-only — don't confuse it with this
page's self-service `/me/phone/verify` and `/me/email/verify`, which any
user can do for themselves by actually receiving a code.

### Polling / special UX
- Telegram link-code box polls **every second** client-side
  (`_pfTgTimer`) to detect once the code has been used, re-checking the
  server every 5th tick (`GET /users/me/telegram`) — auto-closes on link
  or expiry.
- A verified badge for phone/email must be re-derived after every code
  confirm — don't just flip a local flag, re-fetch `/users/me` (existing
  code does this, e.g. `confirmPhoneCode` calls `loadPhoneState()` again).
- Username rename mid-session requires token reissue — the `access_token`
  field in the PATCH response must overwrite the stored session token, or
  the very next request 401s.

---

## 8. Portal requests — «درخواست‌های مشتریان» (admin side, `nav-link-portal`, permission `portal`)

This is the **admin-facing** table inside the main panel
(`section-portal`), distinct from the customer-facing standalone app at
`frontend/portal.html` + `frontend/js/portal.js` (that's the visitor's own
portal, a separate HTML entry point — out of scope here beyond noting it
exists and shares the same `/api/portal/*` router for the visitor-side
routes).

Old panel: `frontend/index.html:3718-3749`,
`frontend/js/app.js:12008-12058` (`loadPortalRequests`,
`updatePortalRequest`). Backend: `app/api/routes/portal.py` (full file
read, admin section `# ── Staff ──` at the bottom).

### Cards / widgets
Single card: status filter (`new`/`in_review`/`matched`/`contacted`/
`closed`), table columns مشتری / خواسته / بودجه / وضعیت / تاریخ / actions.
Each row: inline status `<select>` (fires `PATCH` on change), and — when
the request has already been turned into a CRM customer (`customer_id` set
by `app/crm/portal_bridge.py` at creation time) — a «ملک‌های مناسب» button
that opens the matching-engine results for that customer (shared with the
CRM module, not documented further here).

### Endpoints (`app/api/routes/portal.py`, `/portal` prefix, `# ── Staff ──` section)
| Method | Path | Params/Body | Response | Role |
|---|---|---|---|---|
| GET | `/portal/admin/requests` | query `status?, limit(1-200), offset` | `{items:[{...request, user:{id,full_name,phone,email}}], total}` — one join, not per-row lookups | staff with `portal` permission |
| PATCH | `/portal/admin/requests/{id}` | `PropertyRequestUpdate{status?, admin_note?, matched_property_id?}` | updated request dict; stamps `handled_by` = acting username | staff with `portal` permission |

(Tickets — `/portal/admin/tickets*` — are the **super_admin**-only sibling
already documented in §5a; they share this router but a stricter role.)

### Role/permission
Router mounted with **no** blanket dependency (`portal.router` is
included bare in `app/api/routes/__init__.py:30` because it also serves
unauthenticated/visitor routes on the same router) — each admin route
individually depends on `require_permission("portal")`
(`_portal_staff = Depends(require_permission("portal"))`), matching
`NAV_PERMISSION['nav-link-portal'] = 'portal'` client-side.

### Polling / special UX
- No auto-poll, manual refresh button + filter re-triggers `GET`.
- Budget column renders differently for `rent` vs. purchase deals
  (ودیعه/اجاره ranges vs. a single budget range) — branch on
  `r.deal_type === 'rent'`.
- A request tied to a CRM customer (`customer_id`) shows a small
  «در موتور تطبیق» hint plus the «ملک‌های مناسب» shortcut — this is a live
  cross-link into the CRM matching engine, not just static text.

---

## 9. Hardcoded brand / office / domain / contact details in the old frontend

No backend settings endpoint exists for any of these (see §5f) — every
occurrence below is a literal string baked into HTML/JS and must be
either (a) kept as a literal in the Next.js templates too, or (b) pulled
from a new settings source if one gets built.

### Brand name ("SorinFlow" / "سورین‌فلو")
| File | Line(s) | Context |
|---|---|---|
| `frontend/index.html` | 53 | `<title>SorinFlow — داشبورد مدیریت</title>` |
| `frontend/index.html` | 61 | `<meta name="apple-mobile-web-app-title" content="SorinFlow">` |
| `frontend/index.html` | 103 | Sidebar logo text `<h4>SorinFlow</h4>` |
| `frontend/index.html` | 401 | Second sidebar-collapsed logo `<h6>SorinFlow</h6>` |
| `frontend/js/app.js` | 1275 | `document.title = 'SorinFlow — ورود'` (login page title) |
| `frontend/js/app.js` | 1773 | `document.title = 'SorinFlow — ' + sectionName` (per-section title) |
| `frontend/js/app.js` | 6305, 6715-6813 | SMS-forwarder Android-app setup instructions repeatedly name «SorinFlow Forwarder» (the companion app's own name, menu paths like Settings → Apps → **SorinFlow Forwarder**) |
| `frontend/portal.html` | 6 | `<title>سورین‌فلو — پورتال مشتریان</title>` |
| `frontend/manifest.webmanifest` | 2-4 | `name`, `short_name` (`SorinFlow`), `description` all hardcode the brand |
| `frontend/landing.html` | 43, 576 | JSON-LD `alternateName: "SorinFlow"`; footer `SorinFlow ∞ Data-Driven Real Estate` |

### Domain (`sorinflow.com`)
| File | Line(s) | Context |
|---|---|---|
| `frontend/landing.html` | 8, 20, 23, 30, 41, 48, 57, 61, 63, 64, 79, 80, 83, 87, 107, 496 | canonical URL, OG/Twitter meta, full JSON-LD graph (`@id`s, `url`, `logo`, FAQ answer text naming `sorinflow.com/portal`), a fake browser-chrome screenshot URL bar |
| `frontend/index.html` | 3910, 3919 | Telegram-relay placeholder examples `https://tg.sorinflow.com` (just placeholder text in an `<input placeholder>`, not a live default) |
| `app/config.py` | 26 | `domain: str = Field(default="scc.sorinflow.com", ...)` — **this one is a real, env-overridable default**, surfaced read-only in monitoring's «دامنه» tile and used to build the `profile_url` in the admin verification-nudge email (`app/api/routes/users.py:1510`) |

### Contact email (`info@sorinflow.com`, `support@sorinflow.com`)
| File | Line(s) | Context |
|---|---|---|
| `frontend/landing.html` | 65, 72, 552 | JSON-LD org `email`, contactPoint `email`, and a visible "ایمیل" contact card |
| `frontend/index.html` | 3545, 3554 | **Placeholder text only** in email-settings form fields (`placeholder="مثلاً info@sorinflow.com"`) — not a real default, just an example |
| `frontend/index.html` | 3803 | Maintenance-mode support-email field placeholder `support@sorinflow.com` — same, just an example |

### Verdict for the rebuild
Everything above is a literal string; there is no `/api/public/site` (or
any `/api/settings/*`) endpoint to read brand/office name, domain, or
contact details from. If the Next.js panel needs these to be editable
without a redeploy, that's new backend work — a natural fit would be a
small `AppSetting`-backed endpoint (e.g. `GET/PUT /api/settings/site`),
super_admin-gated like everything else in §5, since `AppSetting` already
exists and is already used the same way for maintenance mode.
