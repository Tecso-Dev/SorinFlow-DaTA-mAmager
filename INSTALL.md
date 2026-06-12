# SorinFlow — راهنمای نصب و راه‌اندازی

## پیش‌نیازها

فقط این دو تا لازمه — Python، PostgreSQL و Redis نیازی نیست (داخل Docker هستن):

| نرم‌افزار | لینک دانلود |
|-----------|-------------|
| Docker Desktop | https://www.docker.com/products/docker-desktop/ |
| Git | https://git-scm.com/downloads |

---

## راه‌اندازی اولیه (یک‌بار)

**۱. کپی فایل تنظیمات:**
```bash
cp .env.example .env
```

**۲. فایل `.env` رو باز کن و اینا رو ویرایش کن:**
```env
SECRET_KEY=یک_رشته_تصادفی_بلند_بنویس
API_KEY=                         # خالی بذار (اختیاری)
DIVAR_PHONE_NUMBER=09xxxxxxxxx   # شماره دیوارت
```

**۳. Build و اجرا:**
```bash
docker compose up -d --build
```

**۴. ورود به پنل:**

آدرس: `http://localhost`

| فیلد | مقدار |
|------|-------|
| نام کاربری | `admin` |
| رمز عبور | `sorinflow2024` |

> ⚠️ بعد از اولین ورود رمز رو از بخش تنظیمات عوض کن!

---

## دستورات روزانه

```bash
# روشن کردن
docker compose up -d

# خاموش کردن
docker compose down

# ری‌استارت بک‌اند
docker compose restart backend

# ری‌بیلد بعد از آپدیت کد
docker compose up -d --build
```

---

## آدرس‌ها بعد از اجرا

| سرویس | آدرس |
|-------|------|
| پنل مدیریت | http://localhost |
| API مستقیم | http://localhost:8000 |
| Swagger Docs | http://localhost:8000/api/docs |

---

## عیب‌یابی

```bash
# لاگ لایو
docker compose logs backend -f

# وضعیت همه سرویس‌ها
docker compose ps

# ری‌ست کامل (⚠️ همه داده‌ها پاک میشه)
docker compose down -v
```
