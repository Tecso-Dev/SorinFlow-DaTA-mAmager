# رلهٔ تلگرام سورین‌فلو (Cloudflare Worker)

سرور سورین‌فلو در ایران است و معمولاً نمی‌تواند مستقیم به `api.telegram.org`
وصل شود. این پوشه یک Worker کلودفلر است که جلوی `api.telegram.org` می‌نشیند؛
چون کلودفلر از ایران هم جواب می‌دهد، سرور به‌جای تلگرام با این Worker حرف
می‌زند و Worker پیام را عیناً به تلگرام می‌رساند و جواب را عیناً برمی‌گرداند.

این راهنما فرض می‌کند قبلاً با Cloudflare Workers کار نکرده‌اید.

## چیزهایی که لازم دارید

- یک حساب رایگان Cloudflare (اگر ندارید: cloudflare.com → Sign up).
- توکن ربات تلگرام (از BotFather) — همان چیزی که در پنل سورین‌فلو، بخش
  بکاپ، وارد می‌شود.
- شناسهٔ عددی همان ربات: قسمت قبل از `:` در توکن. مثلاً اگر توکن
  `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` است، شناسهٔ ربات
  `123456789` است.

## روش ۱: از داشبورد (بدون خط فرمان، ساده‌ترین راه)

1. وارد `dash.cloudflare.com` شوید.
2. از منوی سمت چپ: **Workers & Pages** → **Create** → **Create Worker**.
3. یک اسم بدهید (مثلاً `sorinflow-telegram-relay`) و **Deploy** را بزنید؛
   یک Worker نمونه ساخته می‌شود.
4. روی Worker تازه‌ساز کلیک کنید → **Edit code** (یا **Quick edit**).
5. تمام کد داخل ادیتور را پاک کنید و کل محتوای فایل `worker.js` همین پوشه را
   جای‌گذاری کنید.
6. **Deploy** را بزنید.
7. به تنظیمات Worker برگردید → تب **Settings** → **Variables and Secrets**:
   - یک متغیر جدید بسازید: نام `ALLOWED_BOTS`، نوع **Text**، مقدار همان
     شناسهٔ عددی ربات (اگر چند ربات دارید، با کاما جدا کنید:
     `123456789,987654321`).
   - یک متغیر دیگر بسازید: نام `RELAY_KEY`، نوع را حتماً **Secret** بگذارید
     (نه Text)، و مقدارش را یک رشتهٔ تصادفی و بلند بگذارید — همان چیزی که
     بعداً در پنل سورین‌فلو هم وارد می‌کنید. برای ساختنش می‌توانید در ترمینال
     بزنید: `openssl rand -hex 32`
   - **Save and deploy**.
8. آدرس Worker را از بالای صفحه کپی کنید — چیزی شبیه
   `https://sorinflow-telegram-relay.<your-subdomain>.workers.dev`.

## روش ۲: با wrangler (خط فرمان)

اگر Node نصب است:

```bash
npm install -g wrangler      # یک‌بار، سراسری
cd deploy/telegram-relay
wrangler login                # مرورگر باز می‌شود، حساب کلودفلر را تأیید کنید
wrangler deploy                # همین که در wrangler.toml نوشته را دیپلوی می‌کند
```

بعد رمزها را دستی بگذارید (wrangler.toml عمداً هیچ رمزی ندارد):

```bash
wrangler secret put RELAY_KEY
# مقدار را وقتی می‌پرسد بچسبانید (پیشنهاد: openssl rand -hex 32)
```

و `ALLOWED_BOTS` را یا در `wrangler.toml` زیر `[vars]` بنویسید و دوباره
`wrangler deploy` بزنید، یا از داشبورد (بالا، قدم ۷) اضافه کنید.

آدرس نهایی همان چیزی است که `wrangler deploy` در خروجی چاپ می‌کند.

## تست با curl

قبل از وصل کردن به پنل، مطمئن شوید Worker درست جواب می‌دهد. جای
`<WORKER_URL>`، `<RELAY_KEY>` و `<BOT_TOKEN>` را با مقدار واقعی عوض کنید:

```bash
curl -s -H "X-Relay-Key: <RELAY_KEY>" \
  "https://<WORKER_URL>/bot<BOT_TOKEN>/getMe"
```

جواب درست چیزی شبیه این است (همان چیزی که خود تلگرام برمی‌گرداند):

```json
{"ok":true,"result":{"id":123456789,"is_bot":true,"username":"..."}}
```

چند حالت خطا که باید ببینید تا مطمئن شوید تنظیمات درست است:

```bash
# بدون کلید — باید 401 با relay:"unauthorized" بدهد
curl -s -i "https://<WORKER_URL>/bot<BOT_TOKEN>/getMe"

# کلید غلط — باید همان 401 را بدهد
curl -s -i -H "X-Relay-Key: غلط" "https://<WORKER_URL>/bot<BOT_TOKEN>/getMe"

# شناسهٔ ربات در ALLOWED_BOTS نیست — باید 403 با relay:"forbidden_bot" بدهد
curl -s -i -H "X-Relay-Key: <RELAY_KEY>" "https://<WORKER_URL>/bot000000000:x/getMe"

# مسیر نامربوط — باید 404 بدهد
curl -s -i -H "X-Relay-Key: <RELAY_KEY>" "https://<WORKER_URL>/hello"
```

اگر همهٔ این‌ها همان‌طور که نوشته شد جواب دادند، Worker آماده است.

## چه چیزی در پنل/env سورین‌فلو وارد شود

در پنل، بخش بکاپ → روش ارسال «رله» را انتخاب کنید و:

- **آدرس رله**: همان `https://<WORKER_URL>` (بدون مسیر، بدون `/` انتهایی).
- **کلید رله**: همان مقداری که در `RELAY_KEY` گذاشتید.

یا اگر با متغیر محیطی تنظیم می‌کنید (این روی env همیشه بر پنل مقدم است):

```
TELEGRAM_API_BASE=https://<WORKER_URL>
TELEGRAM_RELAY_KEY=<RELAY_KEY>
```

## نگه‌داری

- برای عوض کردن `RELAY_KEY` یا `ALLOWED_BOTS`: داشبورد → Worker → Settings →
  Variables and Secrets → مقدار تازه → Save and deploy. نیازی به دیپلوی
  دوبارهٔ کد نیست.
- برای دیدن لاگ‌های زندهٔ Worker: داشبورد → Worker → تب **Logs** (یا
  `wrangler tail`). توکن و کلید هرگز در این لاگ‌ها چاپ نمی‌شوند.
- این Worker فقط دو شکل مسیر را قبول می‌کند: `/bot<token>/<method>` و
  `/file/bot<token>/<path>`؛ هر مسیر دیگری ۴۰۴ می‌گیرد.
