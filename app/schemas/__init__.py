"""
SorinFlow Divar Scraper - Pydantic Schemas
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Any
from datetime import datetime


# ============== Property Schemas ==============

class PropertyBase(BaseModel):
    """Base schema for property"""
    title: str
    description: Optional[str] = None
    price: Optional[int] = None
    price_per_meter: Optional[int] = None
    total_price: Optional[int] = None
    rent_price: Optional[int] = None
    deposit: Optional[int] = None
    area: Optional[int] = None
    rooms: Optional[int] = None
    year_built: Optional[int] = None
    floor: Optional[int] = None
    total_floors: Optional[int] = None
    has_elevator: bool = False
    has_parking: bool = False
    has_storage: bool = False
    has_balcony: bool = False
    city_name: Optional[str] = None
    district: Optional[str] = None
    neighborhood: Optional[str] = None
    category_name: Optional[str] = None
    property_type: Optional[str] = None
    listing_type: Optional[str] = None
    corner_type: Optional[str] = None  # نبش


class PropertyCreate(PropertyBase):
    """Schema for creating a property"""
    url: str
    divar_id: str


class PropertyUpdate(BaseModel):
    """Schema for updating a property"""
    title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[int] = None
    is_active: Optional[bool] = None
    # Divar frequently omits these, so they are editable by hand per property
    building_direction: Optional[str] = None   # جهت
    corner_type: Optional[str] = None          # نبش


class PropertyResponse(PropertyBase):
    """Schema for property response"""
    id: int
    tag_number: str
    serial_no: Optional[int] = None
    divar_id: str
    url: str
    phone_number: Optional[str] = None
    seller_name: Optional[str] = None
    owner_phone: Optional[str] = None
    images: List[str] = []
    has_images: bool = False
    thumbnail_url: Optional[str] = None
    features: List[str] = []
    amenities: List[str] = []
    is_active: bool = True
    # Scrape quality. None means the listing predates grading, "" means graded
    # and clean — the two are deliberately different answers.
    quality_score: Optional[float] = None
    quality_issues: Optional[str] = None
    scraped_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class PropertyListResponse(BaseModel):
    """Schema for paginated property list"""
    items: List[PropertyResponse]
    total: int
    page: int
    size: int
    pages: int


class PropertyFilter(BaseModel):
    """Schema for filtering properties"""
    city: Optional[str] = None
    category: Optional[str] = None
    listing_type: Optional[str] = None  # buy, rent
    property_type: Optional[str] = None  # apartment, villa, etc.
    min_price: Optional[int] = None
    max_price: Optional[int] = None
    min_area: Optional[int] = None
    max_area: Optional[int] = None
    min_rooms: Optional[int] = None
    max_rooms: Optional[int] = None
    has_phone: Optional[bool] = None
    search: Optional[str] = None


# ============== Scraping Job Schemas ==============

class ScrapingJobCreate(BaseModel):
    """Schema for creating a scraping job"""
    city: str
    category: str
    # Optional in date mode (posted_date set): empty = the whole day,
    # a number = cap. In normal mode the backend falls back to 100.
    max_items: Optional[int] = None
    download_images: bool = True
    divar_phone: Optional[str] = None
    # Price filters
    min_price: Optional[int] = None
    max_price: Optional[int] = None
    min_deposit: Optional[int] = None
    max_deposit: Optional[int] = None
    min_rent: Optional[int] = None
    max_rent: Optional[int] = None
    min_price_per_meter: Optional[int] = None
    max_price_per_meter: Optional[int] = None
    # Area / rooms filters
    min_area: Optional[int] = None
    max_area: Optional[int] = None
    min_rooms: Optional[int] = None
    max_rooms: Optional[int] = None
    # Feature filters (None = no filter, True = must have, False = must not have)
    has_images: Optional[bool] = None
    has_elevator: Optional[bool] = None
    has_parking: Optional[bool] = None
    has_storage: Optional[bool] = None
    has_balcony: Optional[bool] = None
    # Advertiser type: "personal" | "agency" | None
    advertiser_type: Optional[str] = None
    # Only include listings posted within the last N hours
    max_age_hours: Optional[int] = None
    # Scrape ALL listings posted on this exact date (Gregorian "YYYY-MM-DD");
    # when set, max_items and max_age_hours are ignored
    posted_date: Optional[str] = None
    # Switch to the next saved Divar account every N listings (0 = never,
    # None = use the server default)
    rotate_every: Optional[int] = None


class ScrapingJobResponse(BaseModel):
    """Schema for scraping job response"""
    id: int
    job_id: str
    city_id: Optional[int] = None
    category_id: Optional[int] = None
    city_name: Optional[str] = None
    category_name: Optional[str] = None
    divar_phone: Optional[str] = None
    status: str
    total_pages: int = 0
    scraped_pages: int = 0
    total_items: int = 0
    scraped_items: int = 0
    new_items: int = 0
    updated_items: int = 0
    failed_items: int = 0
    error_message: Optional[str] = None
    progress: float = 0.0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class ScrapingJobList(BaseModel):
    """Schema for list of scraping jobs"""
    items: List[ScrapingJobResponse]
    total: int


# ============== Auth Schemas ==============

class LoginRequest(BaseModel):
    """Schema for login request"""
    phone_number: str = Field(..., pattern=r'^09\d{9}$')


class OTPVerifyRequest(BaseModel):
    """Schema for OTP verification"""
    code: str = Field(..., min_length=6, max_length=6)


class CookieStatusResponse(BaseModel):
    """Schema for cookie status response"""
    has_cookies: bool
    is_valid: bool
    expires_at: Optional[str] = None
    phone_number: str
    message: str


class AuthResponse(BaseModel):
    """Schema for auth response"""
    success: bool
    message: str
    requires_code: bool = False


# ============== Proxy Schemas ==============

class ProxyCreate(BaseModel):
    """Schema for creating a proxy"""
    address: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    protocol: str = "http"


class ProxyResponse(BaseModel):
    """Schema for proxy response"""
    id: int
    address: str
    port: int
    protocol: str
    is_active: bool
    is_working: bool
    fail_count: int = 0
    success_count: int = 0
    avg_response_time: Optional[float] = None
    last_checked: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class ProxyList(BaseModel):
    """Schema for list of proxies"""
    items: List[ProxyResponse]
    total: int


# ============== Statistics Schemas ==============

class DashboardStats(BaseModel):
    """Schema for dashboard statistics"""
    total_properties: int
    properties_with_phone: int
    total_cities: int
    total_categories: int
    active_jobs: int
    properties_today: int
    properties_this_week: int
    city_distribution: List[dict]
    category_distribution: List[dict]
    daily_scraping: List[dict]


class SystemHealth(BaseModel):
    """Schema for system health status"""
    status: str
    database: str
    redis: str
    scraper: str
    cookie_status: str
    uptime: str


# ============== CRN / Lead Schemas ==============

class LeadResponse(BaseModel):
    id: int
    property_id: int
    phone_number: Optional[str] = None
    seller_name: Optional[str] = None
    city_name: Optional[str] = None
    category_name: Optional[str] = None
    listing_type: Optional[str] = None
    price: Optional[int] = None
    area: Optional[int] = None
    property_url: Optional[str] = None
    property_title: Optional[str] = None
    status: str
    notes: Optional[str] = None
    assigned_to: Optional[str] = None
    notified: bool = False
    notified_at: Optional[datetime] = None
    notification_channel: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # District lives on the linked property (not a Lead column)
    district: Optional[str] = None
    # Columns the leads table shows but that live on the linked property.
    # Filled by _attach_property_columns(); None when the property is gone.
    serial_no: Optional[int] = None              # کد ملک — same number as لیست املاک
    scraped_at: Optional[datetime] = None        # تاریخ برداشت آگهی
    price_per_meter: Optional[int] = None        # قیمت هر متر
    document_type: Optional[str] = None          # سند
    has_parking: Optional[bool] = None           # پارکینگ
    has_elevator: Optional[bool] = None          # آسانسور
    building_direction: Optional[str] = None     # جهت
    corner_type: Optional[str] = None            # نبش
    # Full snapshot of the linked property (same data the املاک modal shows).
    # NOT named `property` — that collides with Lead.property (a SQLAlchemy
    # relationship), which from_attributes would try to coerce into a dict.
    property_detail: Optional[dict] = None

    class Config:
        from_attributes = True


class LeadUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    assigned_to: Optional[str] = None
    district: Optional[str] = None


class LeadCreate(BaseModel):
    property_title: str
    # apartment | villa | shop | office
    property_kind: Optional[str] = None
    # uploaded photo URLs (/images/manual/...)
    images: Optional[List[str]] = None
    # structured per-kind fields (متراژ، طبقه، پوشش کف، ...)
    attrs: Optional[dict] = None
    phone_number: Optional[str] = None
    seller_name: Optional[str] = None
    city_name: Optional[str] = None
    category_name: Optional[str] = None
    listing_type: Optional[str] = None
    price: Optional[int] = None
    # rent listings use deposit + monthly rent instead of price
    deposit: Optional[int] = None
    rent_price: Optional[int] = None
    area: Optional[int] = None
    property_url: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    assigned_to: Optional[str] = None


class LeadList(BaseModel):
    items: List[LeadResponse]
    total: int


# ============== User / Auth Schemas ==============

# Single source of truth lives in app/auth/permissions.py so a role added
# there cannot drift from what the schemas will accept.
from app.auth.permissions import VALID_ROLES, ASSIGNABLE_BY_SUPER_ADMIN  # noqa: E402


class TokenResponse(BaseModel):
    access_token: Optional[str] = None
    token_type: str = "bearer"
    role: Optional[str] = None
    username: Optional[str] = None
    full_name: Optional[str] = None
    # 2FA fields — set when TOTP is required instead of issuing a full token
    requires_totp: bool = False
    totp_session: Optional[str] = None


class UserResponse(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    role: str
    is_active: bool
    divar_phone: Optional[str] = None
    phone: Optional[str] = None
    phone_verified: bool = False
    marketing_opt_in: bool = False
    permissions: List[str] = Field(default_factory=list)
    last_login: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    email: Optional[str] = None
    full_name: Optional[str] = None
    password: str = Field(..., min_length=6)
    role: str = Field(default="admin")
    divar_phone: Optional[str] = None
    permissions: Optional[List[str]] = None

    def model_post_init(self, __context: Any) -> None:
        if self.role not in VALID_ROLES:
            raise ValueError(f"role must be one of {VALID_ROLES}")


class UserRegister(BaseModel):
    """Staff account created by a super_admin — not public sign-up.

    Public sign-up lives in app/api/routes/public_auth.py and always produces a
    visitor; this schema is only reachable behind require_super_admin.
    """
    username: str = Field(..., min_length=3, max_length=100)
    full_name: Optional[str] = None
    email: Optional[str] = None
    password: str = Field(..., min_length=6)
    divar_phone: Optional[str] = Field(None, pattern=r'^(09\d{9})?$')


class UserUpdate(BaseModel):
    email: Optional[str] = None
    full_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    divar_phone: Optional[str] = None
    phone: Optional[str] = None
    permissions: Optional[List[str]] = None

    def model_post_init(self, __context: Any) -> None:
        if self.role and self.role not in VALID_ROLES:
            raise ValueError(f"role must be one of {VALID_ROLES}")


class UserPasswordReset(BaseModel):
    new_password: str = Field(..., min_length=6)


class UserList(BaseModel):
    items: List[UserResponse]
    total: int


# ============== TOTP / 2FA Schemas ==============

class TotpSetupResponse(BaseModel):
    secret: str
    qr_uri: str          # otpauth:// URI — feed to qrcode.js on the frontend
    enabled: bool = False


class TotpEnableRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6)


class TotpDisableRequest(BaseModel):
    password: str


class TotpLoginRequest(BaseModel):
    totp_session: str
    code: str = Field(..., min_length=6, max_length=6)


# ============== Portal / public auth Schemas ==============

PHONE_RE = r'^09\d{9}$'
# Deliberately loose but anchored: enough to stop a "username-shaped" value like
# "admin" being stored as an email, which would otherwise collide with a staff
# account in the portal login lookup.
EMAIL_RE = r'^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$'


class PortalRegisterRequest(BaseModel):
    # Both contact fields are mandatory. The phone is what the account is keyed
    # on and what a login code is sent to; the email is the second channel and
    # the one that survives a number change. Optional email meant a visitor
    # could sign up reachable by exactly one route, and with no SMS provider
    # configured that route is currently nothing at all.
    full_name: str = Field(..., min_length=2, max_length=200)
    phone: str = Field(..., pattern=PHONE_RE)
    password: str = Field(..., min_length=6, max_length=128)
    email: str = Field(..., max_length=200, pattern=EMAIL_RE)
    marketing_opt_in: bool = False


class PortalVerifyRequest(BaseModel):
    phone: str = Field(..., pattern=PHONE_RE)
    code: str = Field(..., min_length=4, max_length=8)


class PortalResendRequest(BaseModel):
    phone: str = Field(..., pattern=PHONE_RE)


class PortalLoginRequest(BaseModel):
    """identifier is a phone or an email — visitors may have registered with
    either on screen, and asking them which one they used is friction."""
    identifier: str = Field(..., min_length=3, max_length=200)
    password: str = Field(..., min_length=1, max_length=128)


class PortalPendingResponse(BaseModel):
    pending: bool = True
    channel: str = "sms"
    ttl: int
    cooldown: int
    message: str
    # Which number the code went to. Needed by the login path: someone who
    # signed in with their email has no idea what to send to /verify, and the
    # verify endpoint keys on the phone. Only ever filled in after the password
    # has been checked, so it tells an anonymous caller nothing.
    phone: Optional[str] = None
    debug_code: Optional[str] = None


class PropertyRequestCreate(BaseModel):
    deal_type: str = Field(default="buy")
    property_kind: Optional[str] = Field(None, max_length=30)
    city: Optional[str] = Field(None, max_length=100)
    districts: Optional[str] = Field(None, max_length=500)
    budget_min: Optional[int] = Field(None, ge=0)
    budget_max: Optional[int] = Field(None, ge=0)
    deposit_max: Optional[int] = Field(None, ge=0)
    rent_max: Optional[int] = Field(None, ge=0)
    area_min: Optional[int] = Field(None, ge=0, le=100000)
    area_max: Optional[int] = Field(None, ge=0, le=100000)
    rooms_min: Optional[int] = Field(None, ge=0, le=50)
    year_built_min: Optional[int] = Field(None, ge=1300, le=1500)
    needs_elevator: bool = False
    needs_parking: bool = False
    needs_storage: bool = False
    description: Optional[str] = Field(None, max_length=2000)
    contact_name: Optional[str] = Field(None, max_length=200)
    contact_phone: Optional[str] = Field(None, pattern=r'^(09\d{9})?$')

    def model_post_init(self, __context: Any) -> None:
        if self.deal_type not in {"buy", "rent"}:
            raise ValueError("deal_type must be 'buy' or 'rent'")


class PropertyRequestUpdate(BaseModel):
    status: Optional[str] = None
    admin_note: Optional[str] = Field(None, max_length=2000)
    matched_property_id: Optional[int] = None

    def model_post_init(self, __context: Any) -> None:
        allowed = {"new", "in_review", "matched", "contacted", "closed"}
        if self.status and self.status not in allowed:
            raise ValueError(f"status must be one of {allowed}")


class UpgradeTicketCreate(BaseModel):
    message: Optional[str] = Field(None, max_length=2000)
    contact_phone: Optional[str] = Field(None, pattern=r'^(09\d{9})?$')


class UpgradeTicketDecision(BaseModel):
    approve: bool
    decision_note: Optional[str] = Field(None, max_length=2000)
    permissions: Optional[List[str]] = None
