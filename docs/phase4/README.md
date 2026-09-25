# فاز ۴: ادامهٔ کار روی مک

برانچ: `claude/phase-4-nextjs-frontend-ca3150-p74q6m` (شامل `sorinflow-v2` تا `e1c9670`). وضعیت کلی در `docs/PROGRESS.md` و قاعده‌های صفحه‌ها در `docs/FRONTEND.md`.

## این پوشه
- `inventory-*.md`: فهرست کامل قابلیت‌های پنل قدیم برای هر بخش (endpointها، فیلدها، دسترسی‌ها، رفتارهای خاص). هر بخش تازه باید همهٔ موارد فهرستش را داشته باشد.
  - `inventory-crm.md`: مرحلهٔ ۳ (انجام شد).
  - `inventory-properties-insights.md`: مرحلهٔ ۴.
  - `inventory-scraper-divar.md`: مرحلهٔ ۶.
  - `inventory-system.md`: مرحلهٔ ۷ و ۸ (پیامک، ایمیل، AI، پایش، کاربران و بکاپ، رویدادها، پروفایل، درخواست‌های مشتریان). نکته: بخش ۵f و ۹ آن نوشته‌اند endpoint تنظیمات سایت نیست؛ حالا هست (`GET /api/public/site`، `GET/PUT /api/settings/site`) و فقط صفحهٔ ویرایشش در مرحلهٔ ۷ مانده است.
- `stream-rules.md`: دستورالعملی که به هر ایجنت یک جریان داده شد. مسیرهای `/tmp/...` و `/tmp/venv-sf` مال نشست ابری‌اند؛ روی مک venv `~/.venvs/sorinflow-v2` است و ایجنت‌ها باید اول `git checkout -B <اسم> <همین برانچ>` بزنند.
- `crm-c.spec.draft.js`: تست end-to-end تقویم و کمد و زونکن که نیمه‌کاره ماند: ۱۷ از ۲۱ سبز. «تقویم: loads with data…» روی دسکتاپ و آیفون، و «کمد و زونکن: cabinet and binder…» روی اندروید و آیفون شکست می‌خورند. تمامش کن و به `tests/e2e/next/crm-c.spec.js` برگردان.

## اجرای محلی پنل تازه
```bash
scripts/local_up.sh                       # backend روی 8000 با دادهٔ نمونهٔ ۶۰ روزه
cd frontend-next && npm ci && npx next typegen
BACKEND_INTERNAL_URL=http://127.0.0.1:8000 npx next dev -p 3000 -H 127.0.0.1
# http://127.0.0.1:3000/panel/login  (owner / agent1 / ... با رمز محلی local-pass-1234)
```
اگر backend محلی دادهٔ قدیمی دارد، `docker compose -f docker-compose.local.yml exec backend python scripts/seed_local.py` سابقهٔ ۶۰ روزه را اضافه می‌کند.

## تست‌ها
```bash
# پنل تازه، production build، دسکتاپ و اندروید و آیفون (پایگاه دادهٔ sorinflow_e2e_local را از نو بساز)
PYTHON=~/.venvs/sorinflow-v2/bin/python npx --prefix tests/e2e playwright test --config tests/e2e/playwright.next.config.js
# روی dev server یک بخش: E2E_DEV=1 E2E_NEXT_URL=http://127.0.0.1:3000 ... <spec>
```

## پیش از دیپلوی (هنوز انجام نشده)
1. مرحله‌های مانده: ۴، ۶، ۷، ۸ و صفحهٔ اصلی.
2. شبیه‌سازی کامل k3d طبق CLAUDE.md برای Deployment تازهٔ `web` و Ingress و NetworkPolicyها (`k8s/base/web.yaml`). در نشست ابری ممکن نشد. image ساخته شد و کانتینر پنل با فایل‌سیستم فقط‌خواندنی درست بالا آمد، ولی خود k8s امتحان نشده است.
3. بازبین جداگانهٔ کل diff فاز با نگاه مهاجم.
4. دستور دیپلوی سبحان.
