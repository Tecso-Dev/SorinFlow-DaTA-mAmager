"""
SorinFlow Divar Scraper - Property Models
"""
from sqlalchemy import Column, Integer, String, BigInteger, Boolean, Float, Text, ForeignKey, DateTime, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from datetime import datetime


class City(Base):
    """City model for storing city information"""
    __tablename__ = "cities"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    slug = Column(String(100), nullable=False, unique=True, index=True)
    province = Column(String(100))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    properties = relationship("Property", back_populates="city")
    
    def __repr__(self):
        return f"<City(id={self.id}, name={self.name}, slug={self.slug})>"


class Category(Base):
    """Category model for storing property categories"""
    __tablename__ = "categories"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    slug = Column(String(100), nullable=False, unique=True, index=True)
    parent_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    url_path = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    properties = relationship("Property", back_populates="category")
    parent = relationship("Category", remote_side=[id], backref="children")
    
    def __repr__(self):
        return f"<Category(id={self.id}, name={self.name}, slug={self.slug})>"


class Property(Base):
    """Property model for storing real estate listings"""
    __tablename__ = "properties"
    
    id = Column(Integer, primary_key=True, index=True)
    tag_number = Column(String(50), unique=True, nullable=False, index=True)
    # human-friendly incrementing serial (starts at 1000)
    serial_no = Column(Integer, unique=True, index=True)
    divar_id = Column(String(50), unique=True, nullable=False, index=True)
    title = Column(String(500), nullable=False)
    description = Column(Text)
    
    # Pricing
    price = Column(BigInteger)  # Total price or per meter price
    price_per_meter = Column(BigInteger)
    total_price = Column(BigInteger)
    rent_price = Column(BigInteger)  # Monthly rent
    deposit = Column(BigInteger)  # Deposit for rent
    
    # Property Details
    area = Column(Integer)  # Square meters (built area)
    land_area = Column(Integer)  # Land area in square meters
    built_area = Column(Integer)  # Built/construction area
    rooms = Column(Integer)  # Number of bedrooms
    year_built = Column(Integer)  # Construction year
    floor = Column(Integer)  # Floor number
    total_floors = Column(Integer)  # Total floors in building
    
    # Amenities
    has_elevator = Column(Boolean, default=False)
    has_parking = Column(Boolean, default=False)
    has_storage = Column(Boolean, default=False)
    has_balcony = Column(Boolean, default=False)
    has_images = Column(Boolean, default=False)  # Whether property has images in listing
    
    # Additional Info
    building_direction = Column(String(50))  # North, South, etc.
    corner_type = Column(String(20))  # نبش: تک‌نبش | دونبش | سه‌نبش | چهارنبش
    frontage = Column(Integer)  # Building frontage/width in meters (بر)
    unit_status = Column(String(50))  # Empty, Tenant, Owner
    document_type = Column(String(100))  # Type of ownership document
    usage_type = Column(String(100))  # Type of usage (residential, commercial, etc.)
    building_age = Column(String(50))  # Building age description
    
    # Location
    city_id = Column(Integer, ForeignKey("cities.id"))
    city_name = Column(String(100))
    district = Column(String(200))
    neighborhood = Column(String(200))
    address = Column(Text)
    latitude = Column(Float)
    longitude = Column(Float)
    
    # Category
    category_id = Column(Integer, ForeignKey("categories.id"))
    category_name = Column(String(100))
    property_type = Column(String(50))  # apartment, villa, etc.
    listing_type = Column(String(50))  # buy, rent
    
    # Contact
    phone_number = Column(String(20), index=True)
    seller_name = Column(String(200))
    advertiser_type = Column(String(20))  # personal, agency
    
    # URLs
    url = Column(String(500), nullable=False)
    
    # Images
    images = Column(JSON, default=list)  # List of image URLs
    thumbnail_url = Column(String(500))
    images_downloaded = Column(Boolean, default=False)
    
    # Features and Amenities (JSON)
    features = Column(JSON, default=list)
    amenities = Column(JSON, default=list)
    
    # Structured manual-lead attributes (پوشش کف، کابینت، گرمایش، ...)
    extra_attrs = Column(JSON, default=dict)

    # Owner — which Divar account scraped this property
    owner_phone = Column(String(20), nullable=True, index=True)

    # ── Filing (کمد و زونکن) ──
    binder_id = Column(Integer, ForeignKey("crm_binders.id", ondelete="SET NULL"),
                       nullable=True, index=True)
    is_pinned = Column(Boolean, default=False, index=True)     # سنجاق
    is_archived = Column(Boolean, default=False, index=True)   # بایگانی
    is_private = Column(Boolean, default=False, index=True)    # فایل شخصی
    is_draft = Column(Boolean, default=False, index=True)      # پیش‌نویس
    created_by = Column(String(200), index=True)               # who filed it
    tags = Column(String(500))                                 # برچسب، comma-separated

    # Status
    is_active = Column(Boolean, default=True)
    
    # Timestamps
    posted_at = Column(DateTime(timezone=True))
    scraped_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    city = relationship("City", back_populates="properties")
    category = relationship("Category", back_populates="properties")
    
    def __repr__(self):
        return f"<Property(id={self.id}, tag={self.tag_number}, title={self.title[:30]}...)>"
    
    def to_dict(self):
        """Convert property to dictionary with clean price fields"""
        data = {
            "id": self.id,
            "tag_number": self.tag_number,
            "serial_no": self.serial_no,
            "divar_id": self.divar_id,
            "title": self.title,
            "description": self.description,
            "area": self.area,
            "rooms": self.rooms,
            "year_built": self.year_built,
            "floor": self.floor,
            "total_floors": self.total_floors,
            "has_elevator": self.has_elevator,
            "has_parking": self.has_parking,
            "has_storage": self.has_storage,
            "has_balcony": self.has_balcony,
            "city_name": self.city_name,
            "district": self.district,
            "neighborhood": self.neighborhood,
            "address": self.address,
            "category_name": self.category_name,
            "property_type": self.property_type,
            "listing_type": self.listing_type,
            "corner_type": self.corner_type,
            "phone_number": self.phone_number,
            "seller_name": self.seller_name,
            "url": self.url,
            "images": self.images,
            "thumbnail_url": self.thumbnail_url,
            "features": self.features,
            "amenities": self.amenities,
            "extra_attrs": self.extra_attrs or {},
            "owner_phone": self.owner_phone,
            "is_active": self.is_active,
            "binder_id": self.binder_id,
            "is_pinned": bool(self.is_pinned),
            "is_archived": bool(self.is_archived),
            "is_private": bool(self.is_private),
            "is_draft": bool(self.is_draft),
            "created_by": self.created_by,
            "tags": self.tags,
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
            "scraped_at": self.scraped_at.isoformat() if self.scraped_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        # Only one of these groups should be set based on listing_type
        if self.listing_type == "buy":
            data["price"] = self.price or self.total_price
            data["deposit"] = None
            data["rent_price"] = None
        elif self.listing_type == "rent":
            data["price"] = None
            data["deposit"] = self.deposit
            data["rent_price"] = self.rent_price
        else:
            # fallback: show all if unknown
            data["price"] = self.price or self.total_price
            data["deposit"] = self.deposit
            data["rent_price"] = self.rent_price
        return data


async def allocate_serial_no(db) -> int:
    """Next incrementing property serial (starts at 1000). Concurrency-safe
    enough for our single-writer scraper: reads MAX+1 within the caller's tx."""
    from sqlalchemy import select, func
    current_max = (await db.execute(select(func.max(Property.serial_no)))).scalar()
    return max((current_max or 999) + 1, 1000)
