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


class PropertyResponse(PropertyBase):
    """Schema for property response"""
    id: int
    tag_number: str
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
    max_pages: int = 10
    download_images: bool = True
    divar_phone: Optional[str] = None
    min_price: Optional[int] = None
    max_price: Optional[int] = None
    min_deposit: Optional[int] = None
    max_deposit: Optional[int] = None
    min_rent: Optional[int] = None
    max_rent: Optional[int] = None


class ScrapingJobResponse(BaseModel):
    """Schema for scraping job response"""
    id: int
    job_id: str
    city_id: Optional[int] = None
    category_id: Optional[int] = None
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

    class Config:
        from_attributes = True


class LeadUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    assigned_to: Optional[str] = None


class LeadList(BaseModel):
    items: List[LeadResponse]
    total: int


# ============== User / Auth Schemas ==============

VALID_ROLES = {"super_admin", "admin", "user"}


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
    last_login: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    email: Optional[str] = None
    full_name: Optional[str] = None
    password: str = Field(..., min_length=6)
    role: str = Field(default="user")

    def model_post_init(self, __context: Any) -> None:
        if self.role not in VALID_ROLES:
            raise ValueError(f"role must be one of {VALID_ROLES}")


class UserUpdate(BaseModel):
    email: Optional[str] = None
    full_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None

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
