# SorinFlow — قاعده‌های ثابت برای هر نشست

وضعیت کار و تصمیم‌ها: `docs/PROGRESS.md`. اول آن را بخوان.

## گفتگو
- با سبحان فقط فارسی. هر سؤال یا تصمیم با ابزار سؤال (AskUserQuestion)، نه در متن.

## git و دیپلوی
- برانچ کاری `sorinflow-v2` است و همهٔ کارها روی آن انجام می‌شود.
- هویت کامیت فقط:
  `git config user.name "sobhan azimzadeh"` و `git config user.email "sobhan.gjhav.azimzadeh@gmail.com"`.
  هیچ خط `Co-Authored-By` یا `Claude-Session` و هیچ اشاره‌ای به Claude در پیام کامیت، PR یا فایل‌ها نباشد.
- کامیت‌ها کوچک و زیاد باشند. پیام با `fix:`، `feat:`، `perf:`، `docs:`، `ci:` یا `test:` شروع شود و بگوید چه خراب بود، چه عوض شد و چه چیزی بررسی شد.
- **هرگز به `main` پوش نکن و هرگز دیپلوی نکن.** پوش به `main` یعنی دیپلوی خودکار روی sorinflow.com (`.github/workflows/deploy.yml`)، و سایت کاربر واقعی دارد. پوش و دیپلوی فقط با دستور صریح سبحان انجام می‌شود.
- `gh workflow run` یا «Run workflow» را برای `deploy.yml` اجرا نکن. هر برانچی را که با آن اجرا شود دیپلوی می‌کند، نه فقط main.
- پوش به `sorinflow-v2` مجاز است و آخر هر فاز انجام شود.
- فاز ۰ و ۱ در ۱۴۰۵/۰۷/۰۲ روی سایت دیپلوی شدند (PR ۱۴ و ۱۵، `7dcc96f`). در مخزن فقط `main` و `sorinflow-v2` مانده است. بعد از هر دیپلوی `sorinflow-v2` با `main` هم‌سطح می‌شود. `0009` قدیمی `main` محلی (`cookies.enabled`) هیچ‌وقت دیپلوی نشد و `_migrate_cookie_is_enabled` در `init_db` هر برخوردی را بی‌اثر می‌کند.

## سرور و secretها
- به سرور production وصل نشو. رمز SSH سرور را هرگز عوض یا غیرفعال نکن.
- هیچ رمز یا کلیدی در گفتگو یا فایل‌های مخزن نباشد. secretها فقط در `.env` محلی (در gitignore) یا متغیر محیطی.
- اعتبارنامه‌های پنل: اول env، بعد مقدار رمزشدهٔ پنل. رمز را از سبحان نپرس.
- پوشهٔ `~/SorinFlow-backups` (بکاپ‌های سرور روی این مک، با رمزها و دادهٔ واقعی مشتری‌ها) را هیچ نشست یا ایجنتی بدون اجازهٔ صریح سبحان باز نکند و نخواند، حتی فقط نام کلیدها را. اگر چیزی از آن لازم است، با ابزار سؤال بپرس. در پرامپت هر ایجنت هم این را بنویس.

## پایگاه داده
- همهٔ migrationها افزایشی و سازگار با نسخهٔ قبل باشند (expand/contract): کد نسخهٔ قبلی باید با پایگاه دادهٔ جدید کار کند و دیپلوی بدون قطعی و بدون از دست رفتن داده باشد.
- شناسهٔ revision در Alembic باید یکتا باشد. پیش از ساختن revision تازه، `alembic heads` را روی همهٔ برانچ‌های زنده چک کن. دو فایل با یک revision در تست دیده نمی‌شوند (تست همیشه پایگاه داده را از صفر می‌سازد) و فقط در production خراب می‌کنند.
- `init_db()` در `app/database.py` هنگام بالا آمدن سرور: روی پایگاه دادهٔ تازه جدول‌ها را از مدل‌ها می‌سازد و فقط head را stamp می‌کند، پس `upgrade()` هیچ revisionی در تست‌های معمولی اجرا نمی‌شود (این کار `tests/test_pg_migration.py` است). روی پایگاه دادهٔ موجود `alembic upgrade head` می‌زند. خطای Alembic فقط log می‌شود و سرور سالم بالا می‌آید، پس `/health` سبز دلیل موفقیت migration نیست.
- از فاز ۳ روی سرور این‌طور است: migration فقط در Job جدای `python -m app.migrate` اجرا می‌شود، که سخت‌گیر است و با خطای Alembic شکست می‌خورد. پادها با `DB_MIGRATE_ON_BOOT=false` فقط بررسی می‌کنند که پایگاه داده از head image عقب نباشد. پایگاه دادهٔ جلوتر (بعد از rollback) پذیرفته است، چون migrationها افزایشی‌اند. لوکال و تست‌ها مثل قبل در boot migrate می‌کنند.

## تست
- هیچ تستی را skip یا غیرفعال نکن. تست رفتاری (HTTP یا واحد) بر تستی که فقط متن کد را چک می‌کند مقدم است.
- نیمی از تست‌های auth، شماره‌های دیوار و migration روی sqlite **خودشان skip می‌شوند**. پیش از هر پوش روی Postgres اجرا کن:

```bash
LC_ALL=en_US.UTF-8 /usr/local/opt/postgresql@16/bin/pg_ctl -D /usr/local/var/postgresql@16 -l /tmp/pg.log start
redis-server --daemonize yes --save "" --appendonly no
psql -h localhost -d postgres -tAc "DROP DATABASE IF EXISTS sorinflow_test"; psql -h localhost -d postgres -tAc "CREATE DATABASE sorinflow_test"
DATABASE_URL=postgresql+asyncpg://macbook@localhost:5432/sorinflow_test REDIS_URL=redis://localhost:6379/9 SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_hex(32))") LOGS_PATH=/tmp IMAGES_PATH=/tmp /Users/macbook/.venvs/sorinflow-v2/bin/python -m pytest tests/ -q
PG_TEST_URL=postgresql+asyncpg://macbook@localhost:5432/sorinflow_test SECRET_KEY=x LOGS_PATH=/tmp IMAGES_PATH=/tmp /Users/macbook/.venvs/sorinflow-v2/bin/python -m pytest tests/test_pg_migration.py -q
```

  (بدون `LC_ALL`، Postgres با خطای «postmaster became multithreaded» می‌میرد. پایگاه دادهٔ تست را پیش از هر اجرا از نو بساز، چون نام کاربرها یکتاست. کلید ثابت قبلی (`ci-only-...`) حالا در فهرست کلیدهای منتشرشده است و production با آن بالا نمی‌آید، پس هر بار کلید تصادفی بساز.)
- پیش از کامیت، کد تازه باید از دروازهٔ lint رد شود: `python scripts/lint_new_code.py` (ruff، mypy و eslint، فقط روی خط‌های تغییرکرده). `pre-commit install` همین را خودکار می‌کند. CI همین را اجرا می‌کند و کد قدیمی را برای خطاهای قدیمی‌اش رد نمی‌کند.
- تست end-to-end پنل با Playwright و axe در `tests/e2e/` است (`scripts/e2e_up.sh`) و روی همین مک هم اجرا می‌شود. `@playwright/test` از ۱۴۰۵/۰۷/۰۳ روی 1.63 است. اجرا:
  `PYTHON=~/.venvs/sorinflow-v2/bin/python npx --prefix tests/e2e playwright test --config tests/e2e/playwright.config.js` (پیش از اجرا پایگاه دادهٔ `sorinflow_e2e_local` را از نو بساز).
- ۶ تست fingerprint فقط در CI (Ubuntu) اجرا می‌شوند، چون از Chromium پایتون (Playwright 1.41، همان نسخه‌ای که اسکرپر روی آن تنظیم شده) استفاده می‌کنند و آن نسخه روی macOS این مک بالا نمی‌آید.
- venv این برانچ `~/.venvs/sorinflow-v2` است و بیرون از مخزن قرار دارد. با فایل lock ساخته می‌شود:
  `uv venv --python 3.11 ~/.venvs/sorinflow-v2 && uv pip install --python ~/.venvs/sorinflow-v2/bin/python --require-hashes -r requirements-dev.lock`
  venv پوشهٔ اصلی (`venv/`) نسخه‌های production (FastAPI 0.109) را دارد و مال `main` است. به آن دست نزن.
- وابستگی‌ها: `requirements.txt` ورودی دستی است. `requirements.lock` و `requirements-dev.lock` با `uv pip compile --universal --generate-hashes --python-version 3.10` ساخته می‌شوند (image پایه Python 3.10 دارد). Docker و CI فقط با `--require-hashes` نصب می‌کنند.

## اجرای محلی
- روی این مک فقط یک پشتهٔ Docker باشد: `sorinflow-local` (`docker-compose.local.yml`). Postgres روی 5433، Redis روی 6380 و backend با hot reload روی 8000. Postgres و Redis مخصوص تست از brew روی 5432 و 6379 می‌آیند.
- بالا آوردن و دادهٔ نمونه با یک دستور: `scripts/local_up.sh`. توقف: `docker compose -f docker-compose.local.yml down`. داده هم پاک شود: `... down -v`.
- ورود محلی: `root`، `owner`، `manager1`، `agent1` و `agent2`، همه با رمز دورریختنی `local-pass-1234`. رمز را در مرورگر تایپ نکن: توکن را از `POST /api/users/token` بگیر و در `localStorage.sf_token` بگذار.
- `docker-compose.yml` اصلی برای سرور است (nginx روی 80 و 443). روی لوکال بالا نیاور.
- برنامه یک image با چهار نقش است: `SORINFLOW_ROLE=all|api|worker|scheduler`. پیش‌فرض `all` است، یعنی یک پروسه که همه‌کار می‌کند؛ لوکال و تست‌ها همین را دارند. روی سرور سه Deployment جدا هست: `backend` (api، ۲ نسخه)، `worker` (اسکرپ و Chromium، و مسیرهای ورود دیوار) و `scheduler` (حلقه‌ها). کانتینرها با uid 1000 اجرا می‌شوند.
- هر تغییر در `k8s/`، `scripts/deploy_k8s.sh` یا بخش deploy در `deploy.yml` را پیش از پوش روی k3d امتحان کن. کلاستر: `k3d cluster create sorinflow-test --image rancher/k3s:v1.36.2-k3s1 -p "18080:80@loadbalancer" -p "18443:443@loadbalancer"`. image را با `docker build --platform linux/amd64 --provenance=false` بساز و با `docker save --platform linux/amd64` و `k3d image import` وارد کن. بعد `scripts/deploy_k8s.sh` را با `IMAGE`، `OVERLAY` و `SECRETS_HASH` اجرا کن. شبیه‌سازی کامل فاز ۳ (وضعیت قدیم، گذار اول، دیپلوی معمولی، شکست و بازگشت) هفت خرابی واقعی در دیپلوی پیدا کرد.

## قاعده‌های محصول که کد باید نگه دارد
- هر چیزِ یک حساب دیوار (شماره، کد پیامکی، پنجرهٔ احراز هویت، اعلان) فقط برای صاحب همان شماره است. root مدیریت می‌کند ولی کد کسی را دریافت یا جواب نمی‌دهد.
- مالکیت شمارهٔ دیگران فقط در یک جا عوض می‌شود: کارت فهرست شماره‌ها در «احراز هویت دیوار»، که فقط root آن را دارد و ثبت می‌شود (`PATCH /api/auth/registry/{id}/owner`). ورود به دیوار، وارد کردن کوکی و فورواردر مالک شماره‌ای را که صاحب دارد عوض نمی‌کنند. شمارهٔ بی‌صاحب مال کسی می‌شود که واردش کند، و backfill هنگام بالا آمدن سرور شماره‌های بی‌صاحب را به اولین super_admin می‌دهد. مسیر انتقال دیگری اضافه نکن.
- پنل: RTL و تیره، فقط با tokenهای خود سایت (نه پیش‌فرض Bootstrap). `prompt`، `confirm` و `alert` مرورگر ممنوع است. از ظاهر پنجرهٔ OTP استفاده کن.
- کانتینر tzdata ندارد: `ZoneInfo` هنگام import سرور را از کار انداخت. برای تهران از offset ثابت `+03:30` استفاده کن.

## آمادگی چنددفتری (فقط آمادگی، پیاده‌سازی نه)
- منطق «چه کسی چه چیزی را می‌بیند» فقط در یک helper یا dependency مشترک باشد، نه پخش در routeها.
- اسم برند، دفتر، دامنه و تنظیمات دفتر hardcode نشوند و از تنظیمات خوانده شوند.
- قید یکتایی و ایندکس‌های تازه طوری طراحی شوند که بعداً افزودن ستون `agency_id` به آن‌ها آسان باشد.
- خود چنددفتری تا تصمیم کارفرما پیاده‌سازی نمی‌شود (فاز ۶، آخرین اولویت).

## روش کار
- نشست اصلی هماهنگ‌کننده است. هر فاز به چند جریان مستقل تقسیم می‌شود و هر زیرایجنت در git worktree جدا و با فهرست فایل مشخص کار می‌کند. بعد نتیجه‌ها یکپارچه می‌شوند.
- worktree زیرایجنت از `origin/main` (کد production) ساخته می‌شود، **نه** از `sorinflow-v2`. در پرامپت هر ایجنت بنویس که اول `git checkout -B <اسم> sorinflow-v2` بزند، وگرنه روی کد دیگری کار و تست می‌کند.
- مدل به‌صرفه: جستجو، خواندن کد، اجرای تست، مستندسازی و تغییرات مکانیکی با haiku یا sonnet. طراحی معماری، امنیت و بازبینی نهایی با مدل قوی. حساب روی پلن Max (5x) است و مصرف با سقف پنج‌ساعته و هفتگی آن سنجیده می‌شود. مصرف هر فاز در `docs/PROGRESS.md` ثبت می‌شود.
- پیش از گزارش پایان هر فاز، یک ایجنت بازبین جدا کل diff آن فاز را با نگاه مهاجم بررسی می‌کند (باگ، امنیت، رگرسیون) و یافته‌های تأییدشده رفع می‌شوند.
- آخر هر فاز: `docs/PROGRESS.md` به‌روز شود، `sorinflow-v2` پوش شود، و گزارش فارسی داده شود (چه شد، چه تست شد، کجا را زنده ببینم، اعتبار مصرف‌شده، برنامهٔ فاز بعد). بعد منتظر تأیید بمان.
