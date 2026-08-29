"""
SorinFlow Divar Scraper - Application Configuration
"""
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional, List
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # App Info
    app_name: str = "SorinFlow Divar Scraper"
    app_version: str = "1.0.0"
    environment: str = Field(default="production", env="ENVIRONMENT")
    debug: bool = Field(default=False, env="DEBUG")
    
    # Server
    server_ip: str = Field(default="171.22.182.91", env="SERVER_IP")
    domain: str = Field(default="scc.sorinflow.com", env="DOMAIN")
    domain_dns_only: str = Field(default="sc.sorinflow.com", env="DOMAIN_DNS_ONLY")
    
    # Database
    # No working default on purpose. It used to carry a real password that was
    # published in this repo, so a misconfigured deploy would connect anyway and
    # look healthy. Now an unset DATABASE_URL fails to connect and says so.
    database_url: str = Field(
        default="postgresql+asyncpg://sorinflow:CHANGE_ME@db:5432/divar_scraper",
        env="DATABASE_URL"
    )
    
    # Redis
    redis_url: str = Field(
        default="redis://:CHANGE_ME@redis:6379/0",
        env="REDIS_URL"
    )
    
    # Security
    secret_key: str = Field(
        default="your-super-secret-key-change-in-production",
        env="SECRET_KEY"
    )
    api_key: str = Field(default="", env="API_KEY")
    access_token_expire_minutes: int = 60 * 24  # 24 hours — re-login daily

    # CORS
    cors_origins: str = Field(default="*", env="CORS_ORIGINS")
    
    # Scraper Settings
    scraper_headless: bool = Field(default=True, env="SCRAPER_HEADLESS")
    scraper_timeout: int = Field(default=60000, env="SCRAPER_TIMEOUT")
    scraper_delay_min: float = Field(default=2.0, env="SCRAPER_DELAY_MIN")
    scraper_delay_max: float = Field(default=5.0, env="SCRAPER_DELAY_MAX")
    # Max seconds to wait for a Divar SMS-OTP code before giving up on a phone.
    # 120 was not enough: the SMS itself can take a minute, the dashboard only
    # polls for the prompt every 4s, and then someone has to read and type it.
    # The code would be entered against a request the scraper had already
    # dropped, and come back "no pending OTP request".
    otp_wait_timeout: int = Field(default=300, env="OTP_WAIT_TIMEOUT")
    # How many listings one saved Divar account handles before the scraper
    # switches to the next. Counted per listing *opened*, not per listing
    # saved, so a filtered-out ad costs the account the same as a kept one.
    # 0 = never rotate.
    cookie_rotate_every: int = Field(default=100, env="COOKIE_ROTATE_EVERY")
    
    # Image download limits. A listing's photo list comes from Divar and is not
    # something we control, so both the count and the size of each file are
    # capped rather than trusted.
    max_images_per_property: int = Field(default=20, env="MAX_IMAGES_PER_PROPERTY")
    max_image_bytes: int = Field(default=8 * 1024 * 1024, env="MAX_IMAGE_BYTES")
    # Decoded pixel ceiling. A few hundred KB of PNG can decode to gigabytes of
    # bitmap — the classic decompression bomb. 40MP is far above any real estate
    # photo and far below anything that hurts.
    max_image_pixels: int = Field(default=40_000_000, env="MAX_IMAGE_PIXELS")

    # Proxy Settings
    proxy_enabled: bool = Field(default=False, env="PROXY_ENABLED")
    proxy_list: str = Field(default="", env="PROXY_LIST")
    
    # Divar Login
    divar_phone_number: str = Field(default="", env="DIVAR_PHONE_NUMBER")
    
    # Paths
    cookies_path: str = "/app/data/cookies"
    images_path: str = "/app/data/images"
    logs_path: str = "/app/logs"
    
    # Divar URLs
    divar_base_url: str = "https://divar.ir"
    divar_login_url: str = "https://divar.ir/my-divar/my-posts"

    # Cities Configuration
    default_city: str = "urmia"

    # Default super admin (created on first startup if no users exist)
    super_admin_username: str = Field(default="admin", env="SUPER_ADMIN_USERNAME")
    # Seeded only when the users table is empty. The old default was published
    # in this repo and in INSTALL.md, so anyone could read it.
    super_admin_password: str = Field(default="CHANGE_ME", env="SUPER_ADMIN_PASSWORD")

    # SMS — Kavenegar
    kavenegar_api_key: str = Field(default="", env="KAVENEGAR_API_KEY")
    kavenegar_sender: str = Field(default="", env="KAVENEGAR_SENDER")

    # SMS — Melipayamak
    melipayamak_api_key: str = Field(default="", env="MELIPAYAMAK_API_KEY")
    melipayamak_from: str = Field(default="", env="MELIPAYAMAK_FROM")

    # LLM (optional) — powers the AI reasons in property matching.
    # Any OpenAI-compatible endpoint works (OpenAI, OpenRouter, local vLLM…).
    llm_api_key: str = Field(default="", env="LLM_API_KEY")
    llm_base_url: str = Field(default="https://api.openai.com/v1", env="LLM_BASE_URL")
    llm_model: str = Field(default="gpt-4o-mini", env="LLM_MODEL")

    # CRN — Telegram notification
    telegram_bot_token: str = Field(default="", env="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", env="TELEGRAM_CHAT_ID")

    # ── Public portal auth (visitor sign-up) ──────────────────────────────
    # OFF until the Iranian SMS panel is provisioned. While it is off the
    # public endpoints answer 404, so the live panel is untouched and no user
    # can reach a sign-up whose verification code would never arrive.
    public_auth_enabled: bool = Field(default=False, env="PUBLIC_AUTH_ENABLED")
    # Which SMS provider carries the login/registration code.
    auth_sms_provider: str = Field(default="kavenegar", env="AUTH_SMS_PROVIDER")
    # Verification code: length, lifetime, resend cooldown, and how many wrong
    # guesses a single code tolerates before it is burned.
    auth_code_length: int = Field(default=5, env="AUTH_CODE_LENGTH")
    auth_code_ttl_seconds: int = Field(default=180, env="AUTH_CODE_TTL_SECONDS")
    auth_code_resend_cooldown: int = Field(default=90, env="AUTH_CODE_RESEND_COOLDOWN")
    auth_code_max_attempts: int = Field(default=5, env="AUTH_CODE_MAX_ATTEMPTS")
    auth_code_max_sends_per_hour: int = Field(default=5, env="AUTH_CODE_MAX_SENDS_PER_HOUR")
    # Failed password attempts per identifier per 15 minutes before lockout.
    auth_login_max_attempts: int = Field(default=10, env="AUTH_LOGIN_MAX_ATTEMPTS")

    # Root account — the developer's own access. Seeded on boot, never
    # creatable from the panel. Leave the password empty to skip seeding.
    root_username: str = Field(default="root", env="ROOT_USERNAME")
    root_password: str = Field(default="", env="ROOT_PASSWORD")
    root_email: str = Field(default="", env="ROOT_EMAIL")

    # CRN — Email (SMTP) notification
    smtp_host: str = Field(default="", env="SMTP_HOST")
    smtp_port: int = Field(default=587, env="SMTP_PORT")
    smtp_user: str = Field(default="", env="SMTP_USER")
    smtp_password: str = Field(default="", env="SMTP_PASSWORD")
    notification_email: str = Field(default="", env="NOTIFICATION_EMAIL")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"  # Ignore extra environment variables
    
    @property
    def proxy_servers(self) -> List[str]:
        """Parse proxy list into individual proxies"""
        if not self.proxy_list:
            return []
        return [p.strip() for p in self.proxy_list.split(",") if p.strip()]


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()


# City slugs mapping — matches Divar.ir URL slugs
CITIES = {
    # تهران
    "tehran":           {"name": "تهران",           "province": "تهران"},
    "karaj":            {"name": "کرج",              "province": "البرز"},
    "eslamshahr":       {"name": "اسلامشهر",         "province": "تهران"},
    "shahriar":         {"name": "شهریار",            "province": "تهران"},
    "varamin":          {"name": "ورامین",            "province": "تهران"},
    "pakdasht":         {"name": "پاکدشت",            "province": "تهران"},
    "malard":           {"name": "ملارد",             "province": "البرز"},
    "robat-karim":      {"name": "رباط‌کریم",         "province": "تهران"},
    "hashtgerd":        {"name": "هشتگرد",            "province": "البرز"},
    # آذربایجان شرقی
    "tabriz":           {"name": "تبریز",             "province": "آذربایجان شرقی"},
    "maragheh":         {"name": "مراغه",             "province": "آذربایجان شرقی"},
    "marand":           {"name": "مرند",              "province": "آذربایجان شرقی"},
    "ahar":             {"name": "اهر",               "province": "آذربایجان شرقی"},
    "sarab":            {"name": "سراب",              "province": "آذربایجان شرقی"},
    "bonab":            {"name": "بناب",              "province": "آذربایجان شرقی"},
    "malekan":          {"name": "ملکان",             "province": "آذربایجان شرقی"},
    "miandoab":         {"name": "میاندوآب",           "province": "آذربایجان شرقی"},
    "shabestar":        {"name": "شبستر",             "province": "آذربایجان شرقی"},
    # آذربایجان غربی
    "urmia":            {"name": "ارومیه",            "province": "آذربایجان غربی"},
    "khoy":             {"name": "خوی",               "province": "آذربایجان غربی"},
    "mahabad":          {"name": "مهاباد",            "province": "آذربایجان غربی"},
    "bukan":            {"name": "بوکان",             "province": "آذربایجان غربی"},
    "sardasht":         {"name": "سردشت",             "province": "آذربایجان غربی"},
    "salmas":           {"name": "سلماس",             "province": "آذربایجان غربی"},
    "mako":             {"name": "ماکو",              "province": "آذربایجان غربی"},
    "piranshahr":       {"name": "پیرانشهر",          "province": "آذربایجان غربی"},
    # اردبیل
    "ardabil":          {"name": "اردبیل",            "province": "اردبیل"},
    "parsabad":         {"name": "پارس‌آباد",         "province": "اردبیل"},
    "meshginshahr":     {"name": "مشگین‌شهر",         "province": "اردبیل"},
    "khalkhal":         {"name": "خلخال",             "province": "اردبیل"},
    "germi":            {"name": "گرمی",              "province": "اردبیل"},
    # اصفهان
    "isfahan":          {"name": "اصفهان",            "province": "اصفهان"},
    "kashan":           {"name": "کاشان",             "province": "اصفهان"},
    "najafabad":        {"name": "نجف‌آباد",          "province": "اصفهان"},
    "khomeinishahr":    {"name": "خمینی‌شهر",         "province": "اصفهان"},
    "shahinshahr":      {"name": "شاهین‌شهر",         "province": "اصفهان"},
    "mobarakeh":        {"name": "مبارکه",            "province": "اصفهان"},
    "ardestan":         {"name": "اردستان",           "province": "اصفهان"},
    "golpayegan":       {"name": "گلپایگان",          "province": "اصفهان"},
    # البرز — karaj و hashtgerd بالا
    # ایلام
    "ilam":             {"name": "ایلام",             "province": "ایلام"},
    "dehloran":         {"name": "دهلران",            "province": "ایلام"},
    "abdanan":          {"name": "آبدانان",           "province": "ایلام"},
    "mehran":           {"name": "مهران",             "province": "ایلام"},
    # بوشهر
    "bushehr":          {"name": "بوشهر",             "province": "بوشهر"},
    "borazjan":         {"name": "برازجان",           "province": "بوشهر"},
    "bandar-genaveh":   {"name": "بندر گناوه",        "province": "بوشهر"},
    "bandar-kangan":    {"name": "بندر کنگان",        "province": "بوشهر"},
    # تهران — بالا
    # چهارمحال
    "shahr-kord":       {"name": "شهرکرد",            "province": "چهارمحال و بختیاری"},
    "borujen":          {"name": "بروجن",             "province": "چهارمحال و بختیاری"},
    "farrokhshahr":     {"name": "فرخشهر",            "province": "چهارمحال و بختیاری"},
    "lordegan":         {"name": "لردگان",            "province": "چهارمحال و بختیاری"},
    # خراسان جنوبی
    "birjand":          {"name": "بیرجند",            "province": "خراسان جنوبی"},
    "tabas":            {"name": "طبس",               "province": "خراسان جنوبی"},
    "ferdows":          {"name": "فردوس",             "province": "خراسان جنوبی"},
    # خراسان رضوی
    "mashhad":          {"name": "مشهد",              "province": "خراسان رضوی"},
    "neyshabur":        {"name": "نیشابور",           "province": "خراسان رضوی"},
    "sabzevar":         {"name": "سبزوار",            "province": "خراسان رضوی"},
    "torbat-heydariyeh":{"name": "تربت حیدریه",       "province": "خراسان رضوی"},
    "kashmar":          {"name": "کاشمر",             "province": "خراسان رضوی"},
    "quchan":           {"name": "قوچان",             "province": "خراسان رضوی"},
    "gonabad":          {"name": "گناباد",            "province": "خراسان رضوی"},
    "fariman":          {"name": "فریمان",            "province": "خراسان رضوی"},
    # خراسان شمالی
    "bojnurd":          {"name": "بجنورد",            "province": "خراسان شمالی"},
    "shirvan":          {"name": "شیروان",            "province": "خراسان شمالی"},
    "esfarayen":        {"name": "اسفراین",           "province": "خراسان شمالی"},
    # خوزستان
    "ahvaz":            {"name": "اهواز",             "province": "خوزستان"},
    "abadan":           {"name": "آبادان",            "province": "خوزستان"},
    "khorramshahr":     {"name": "خرمشهر",            "province": "خوزستان"},
    "dezful":           {"name": "دزفول",             "province": "خوزستان"},
    "masjed-soleyman":  {"name": "مسجد سلیمان",       "province": "خوزستان"},
    "andimeshk":        {"name": "اندیمشک",           "province": "خوزستان"},
    "behbahan":         {"name": "بهبهان",            "province": "خوزستان"},
    "omidiyeh":         {"name": "امیدیه",            "province": "خوزستان"},
    "bandar-imam":      {"name": "بندر امام",         "province": "خوزستان"},
    "shushtar":         {"name": "شوشتر",             "province": "خوزستان"},
    "izeh":             {"name": "ایذه",              "province": "خوزستان"},
    "ramhormoz":        {"name": "رامهرمز",           "province": "خوزستان"},
    # زنجان
    "zanjan":           {"name": "زنجان",             "province": "زنجان"},
    "abhar":            {"name": "ابهر",              "province": "زنجان"},
    "khodabandeh":      {"name": "خدابنده",           "province": "زنجان"},
    # سمنان
    "semnan":           {"name": "سمنان",             "province": "سمنان"},
    "shahrud":          {"name": "شاهرود",            "province": "سمنان"},
    "damghan":          {"name": "دامغان",            "province": "سمنان"},
    "garmsar":          {"name": "گرمسار",            "province": "سمنان"},
    # سیستان و بلوچستان
    "zahedan":          {"name": "زاهدان",            "province": "سیستان و بلوچستان"},
    "zabol":            {"name": "زابل",              "province": "سیستان و بلوچستان"},
    "iranshahr":        {"name": "ایرانشهر",          "province": "سیستان و بلوچستان"},
    "chabahar":         {"name": "چابهار",            "province": "سیستان و بلوچستان"},
    "khash":            {"name": "خاش",               "province": "سیستان و بلوچستان"},
    "saravan":          {"name": "سراوان",            "province": "سیستان و بلوچستان"},
    # فارس
    "shiraz":           {"name": "شیراز",             "province": "فارس"},
    "marvdasht":        {"name": "مرودشت",            "province": "فارس"},
    "kazerun":          {"name": "کازرون",            "province": "فارس"},
    "fasa":             {"name": "فسا",               "province": "فارس"},
    "jahrom":           {"name": "جهرم",              "province": "فارس"},
    "lar":              {"name": "لار",               "province": "فارس"},
    "firozabad":        {"name": "فیروزآباد",         "province": "فارس"},
    "abadeh":           {"name": "آباده",             "province": "فارس"},
    # قزوین
    "qazvin":           {"name": "قزوین",             "province": "قزوین"},
    "takestan":         {"name": "تاکستان",           "province": "قزوین"},
    "alvand":           {"name": "الوند",             "province": "قزوین"},
    # قم
    "qom":              {"name": "قم",                "province": "قم"},
    # کردستان
    "sanandaj":         {"name": "سنندج",             "province": "کردستان"},
    "marivan":          {"name": "مریوان",            "province": "کردستان"},
    "saqqez":           {"name": "سقز",               "province": "کردستان"},
    "bijar":            {"name": "بیجار",             "province": "کردستان"},
    "baneh":            {"name": "بانه",              "province": "کردستان"},
    "qorveh":           {"name": "قروه",              "province": "کردستان"},
    # کرمان
    "kerman":           {"name": "کرمان",             "province": "کرمان"},
    "rafsanjan":        {"name": "رفسنجان",           "province": "کرمان"},
    "sirjan":           {"name": "سیرجان",            "province": "کرمان"},
    "jiroft":           {"name": "جیرفت",             "province": "کرمان"},
    "zarand":           {"name": "زرند",              "province": "کرمان"},
    "bam":              {"name": "بم",                "province": "کرمان"},
    "bardsir":          {"name": "بردسیر",            "province": "کرمان"},
    # کرمانشاه
    "kermanshah":       {"name": "کرمانشاه",          "province": "کرمانشاه"},
    "islamabad-gharb":  {"name": "اسلام‌آباد غرب",    "province": "کرمانشاه"},
    "kangavar":         {"name": "کنگاور",            "province": "کرمانشاه"},
    "harsin":           {"name": "هرسین",             "province": "کرمانشاه"},
    "paveh":            {"name": "پاوه",              "province": "کرمانشاه"},
    # کهگیلویه
    "yasuj":            {"name": "یاسوج",             "province": "کهگیلویه و بویراحمد"},
    "dehdasht":         {"name": "دهدشت",             "province": "کهگیلویه و بویراحمد"},
    "gachsaran":        {"name": "گچساران",           "province": "کهگیلویه و بویراحمد"},
    # گلستان
    "gorgan":           {"name": "گرگان",             "province": "گلستان"},
    "gonbad-kavus":     {"name": "گنبد کاووس",        "province": "گلستان"},
    "aliabad":          {"name": "علی‌آباد کتول",      "province": "گلستان"},
    "minoodasht":       {"name": "مینودشت",           "province": "گلستان"},
    # گیلان
    "rasht":            {"name": "رشت",               "province": "گیلان"},
    "bandar-anzali":    {"name": "بندر انزلی",        "province": "گیلان"},
    "lahijan":          {"name": "لاهیجان",           "province": "گیلان"},
    "langrud":          {"name": "لنگرود",            "province": "گیلان"},
    "rudsar":           {"name": "رودسر",             "province": "گیلان"},
    "fooman":           {"name": "فومن",              "province": "گیلان"},
    "talesh":           {"name": "تالش",              "province": "گیلان"},
    "astara":           {"name": "آستارا",            "province": "گیلان"},
    "shaft":            {"name": "شفت",               "province": "گیلان"},
    # لرستان
    "khorramabad":      {"name": "خرم‌آباد",          "province": "لرستان"},
    "borujerd":         {"name": "بروجرد",            "province": "لرستان"},
    "dorud":            {"name": "دورود",             "province": "لرستان"},
    "kuhdasht":         {"name": "کوهدشت",            "province": "لرستان"},
    "aligudarz":        {"name": "الیگودرز",          "province": "لرستان"},
    # مازندران
    "sari":             {"name": "ساری",              "province": "مازندران"},
    "babol":            {"name": "بابل",              "province": "مازندران"},
    "amol":             {"name": "آمل",               "province": "مازندران"},
    "qaemshahr":        {"name": "قائم‌شهر",          "province": "مازندران"},
    "neka":             {"name": "نکا",               "province": "مازندران"},
    "nowshahr":         {"name": "نوشهر",             "province": "مازندران"},
    "chalus":           {"name": "چالوس",             "province": "مازندران"},
    "ramsar":           {"name": "رامسر",             "province": "مازندران"},
    "tonekabon":        {"name": "تنکابن",            "province": "مازندران"},
    "babolsar":         {"name": "بابلسر",            "province": "مازندران"},
    "mahmudabad":       {"name": "محمودآباد",         "province": "مازندران"},
    # مرکزی
    "arak":             {"name": "اراک",              "province": "مرکزی"},
    "saveh":            {"name": "ساوه",              "province": "مرکزی"},
    "mahallat":         {"name": "محلات",             "province": "مرکزی"},
    "khomein":          {"name": "خمین",              "province": "مرکزی"},
    "shazand":          {"name": "شازند",             "province": "مرکزی"},
    "delijan":          {"name": "دلیجان",            "province": "مرکزی"},
    # هرمزگان
    "bandar-abbas":     {"name": "بندرعباس",          "province": "هرمزگان"},
    "kish":             {"name": "کیش",               "province": "هرمزگان"},
    "minab":            {"name": "میناب",             "province": "هرمزگان"},
    "qeshm":            {"name": "قشم",               "province": "هرمزگان"},
    "bandar-lengeh":    {"name": "بندر لنگه",         "province": "هرمزگان"},
    "bandar-jask":      {"name": "جاسک",              "province": "هرمزگان"},
    # همدان
    "hamadan":          {"name": "همدان",             "province": "همدان"},
    "malayer":          {"name": "ملایر",             "province": "همدان"},
    "nahavand":         {"name": "نهاوند",            "province": "همدان"},
    "tuyserkan":        {"name": "تویسرکان",          "province": "همدان"},
    "asadabad":         {"name": "اسدآباد",           "province": "همدان"},
    # یزد
    "yazd":             {"name": "یزد",               "province": "یزد"},
    "ardakan":          {"name": "اردکان",            "province": "یزد"},
    "meybod":           {"name": "میبد",              "province": "یزد"},
    "bafq":             {"name": "بافق",              "province": "یزد"},
    "taft":             {"name": "تفت",               "province": "یزد"},
}

# Categories configuration
CATEGORIES = {
    "buy-residential": {"name": "خرید مسکونی", "type": "buy"},
    "buy-apartment": {"name": "خرید آپارتمان", "type": "buy"},
    "buy-villa": {"name": "خرید ویلا", "type": "buy"},
    "buy-old-house": {"name": "خرید خانه کلنگی", "type": "buy"},
    "rent-residential": {"name": "اجاره مسکونی", "type": "rent"},
    "rent-apartment": {"name": "اجاره آپارتمان", "type": "rent"},
    "rent-villa": {"name": "اجاره ویلا", "type": "rent"},
    "buy-commercial-property": {"name": "خرید اداری و تجاری", "type": "buy"},
    "buy-office": {"name": "خرید دفتر کار", "type": "buy"},
    "buy-store": {"name": "خرید مغازه", "type": "buy"},
    "buy-industrial-agricultural-property": {"name": "خرید صنعتی و کشاورزی", "type": "buy"},
    "rent-commercial-property": {"name": "اجاره اداری و تجاری", "type": "rent"},
    "rent-office": {"name": "اجاره دفتر کار", "type": "rent"},
    "rent-store": {"name": "اجاره مغازه", "type": "rent"},
    "rent-industrial-agricultural-property": {"name": "اجاره صنعتی و کشاورزی", "type": "rent"},
    "rent-temporary": {"name": "اجاره کوتاه مدت", "type": "rent"},
    "real-estate-services": {"name": "خدمات املاک", "type": "service"},
}
