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
- پوش به `sorinflow-v2` مجاز است و آخر هر فاز انجام شود.
- `main` محلی دو کامیت پوش‌نشده دارد (`dd87ae7` و `beaf285`) که در `sorinflow-v2` ادغام شده‌اند. `main` محلی را هرگز مستقیم پوش نکن، چون migration `0009` آن با این برانچ یکی است ولی ستون دیگری می‌سازد.

## سرور و secretها
- به سرور production وصل نشو. رمز SSH سرور را هرگز عوض یا غیرفعال نکن.
- هیچ رمز یا کلیدی در گفتگو یا فایل‌های مخزن نباشد. secretها فقط در `.env` محلی (در gitignore) یا متغیر محیطی.
- اعتبارنامه‌های پنل: اول env، بعد مقدار رمزشدهٔ پنل. رمز را از سبحان نپرس.

## پایگاه داده
- همهٔ migrationها افزایشی و سازگار با نسخهٔ قبل باشند (expand/contract): کد نسخهٔ قبلی باید با پایگاه دادهٔ جدید کار کند و دیپلوی بدون قطعی و بدون از دست رفتن داده باشد.
- شناسهٔ revision در Alembic باید یکتا باشد. پیش از ساختن revision تازه، `alembic heads` را روی همهٔ برانچ‌های زنده چک کن. دو فایل با یک revision در تست دیده نمی‌شوند (تست همیشه پایگاه داده را از صفر می‌سازد) و فقط در production خراب می‌کنند.
- `init_db()` در `app/database.py` هنگام بالا آمدن سرور `alembic upgrade head` را اجرا می‌کند.

## تست
- هیچ تستی را skip یا غیرفعال نکن. تست رفتاری (HTTP یا واحد) بر تستی که فقط متن کد را چک می‌کند مقدم است.
- نیمی از تست‌های auth، شماره‌های دیوار و migration روی sqlite **خودشان skip می‌شوند**. پیش از هر پوش روی Postgres اجرا کن:

```bash
LC_ALL=en_US.UTF-8 /usr/local/opt/postgresql@16/bin/pg_ctl -D /usr/local/var/postgresql@16 -l /tmp/pg.log start
redis-server --daemonize yes --save "" --appendonly no
psql -h localhost -d postgres -tAc "DROP DATABASE IF EXISTS sorinflow_test"; psql -h localhost -d postgres -tAc "CREATE DATABASE sorinflow_test"
DATABASE_URL=postgresql+asyncpg://macbook@localhost:5432/sorinflow_test REDIS_URL=redis://localhost:6379/9 SECRET_KEY=ci-only-not-a-real-secret-0123456789 LOGS_PATH=/tmp IMAGES_PATH=/tmp /Users/macbook/Documents/SorinFlow-DaTA-mAmager/venv/bin/python -m pytest tests/ -q
PG_TEST_URL=postgresql+asyncpg://macbook@localhost:5432/sorinflow_test SECRET_KEY=x LOGS_PATH=/tmp IMAGES_PATH=/tmp /Users/macbook/Documents/SorinFlow-DaTA-mAmager/venv/bin/python -m pytest tests/test_pg_migration.py -q
```

  (بدون `LC_ALL`، Postgres با خطای «postmaster became multithreaded» می‌میرد. پایگاه دادهٔ تست را پیش از هر اجرا از نو بساز، چون نام کاربرها یکتاست. venv در ریشهٔ مخزن اصلی است، نه در worktreeها.)

## قاعده‌های محصول که کد باید نگه دارد
- هر چیزِ یک حساب دیوار (شماره، کد پیامکی، پنجرهٔ احراز هویت، اعلان) فقط برای صاحب همان شماره است. root مدیریت می‌کند ولی کد کسی را دریافت یا جواب نمی‌دهد. مالکیت شماره را هیچ endpoint یا دستی جابه‌جا نمی‌کند. فقط کسی که کد پیامک دیوار را وارد کند مالک می‌شود.
- پنل: RTL و تیره، فقط با tokenهای خود سایت (نه پیش‌فرض Bootstrap). `prompt`، `confirm` و `alert` مرورگر ممنوع است. از ظاهر پنجرهٔ OTP استفاده کن.
- کانتینر tzdata ندارد: `ZoneInfo` هنگام import سرور را از کار انداخت. برای تهران از offset ثابت `+03:30` استفاده کن.

## آمادگی چنددفتری (فقط آمادگی، پیاده‌سازی نه)
- منطق «چه کسی چه چیزی را می‌بیند» فقط در یک helper یا dependency مشترک باشد، نه پخش در routeها.
- اسم برند، دفتر، دامنه و تنظیمات دفتر hardcode نشوند و از تنظیمات خوانده شوند.
- قید یکتایی و ایندکس‌های تازه طوری طراحی شوند که بعداً افزودن ستون `agency_id` به آن‌ها آسان باشد.
- خود چنددفتری تا تصمیم کارفرما پیاده‌سازی نمی‌شود (فاز ۶، آخرین اولویت).

## روش کار
- نشست اصلی هماهنگ‌کننده است. هر فاز به چند جریان مستقل تقسیم می‌شود و هر زیرایجنت در git worktree جدا و با فهرست فایل مشخص کار می‌کند. بعد نتیجه‌ها یکپارچه می‌شوند.
- مدل به‌صرفه: جستجو، خواندن کد، اجرای تست، مستندسازی و تغییرات مکانیکی با haiku یا sonnet. طراحی معماری، امنیت و بازبینی نهایی با مدل قوی. کل بودجه حدود ۲۰۰ دلار است.
- پیش از گزارش پایان هر فاز، یک ایجنت بازبین جدا کل diff آن فاز را با نگاه مهاجم بررسی می‌کند (باگ، امنیت، رگرسیون) و یافته‌های تأییدشده رفع می‌شوند.
- آخر هر فاز: `docs/PROGRESS.md` به‌روز شود، `sorinflow-v2` پوش شود، و گزارش فارسی داده شود (چه شد، چه تست شد، کجا را زنده ببینم، اعتبار مصرف‌شده، برنامهٔ فاز بعد). بعد منتظر تأیید بمان.
