"""
SorinFlow Divar Scraper - CRM Lead Model
"""
from sqlalchemy import Column, Integer, String, BigInteger, Boolean, Text, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class Lead(Base):
    """CRM lead created from a scraped property"""
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False, index=True)

    # Contact info (copied from property for quick access)
    phone_number = Column(String(20), index=True)
    seller_name = Column(String(200))

    # Property summary
    city_name = Column(String(100))
    category_name = Column(String(100))
    listing_type = Column(String(50))
    price = Column(BigInteger)
    area = Column(Integer)
    property_url = Column(String(500))
    property_title = Column(String(500))

    # CRM status
    status = Column(String(50), default="new", index=True)
    # new | contacted | qualified | closed | rejected
    notes = Column(Text)
    assigned_to = Column(String(200))

    # اجاره داده شده — when the lease ends (1 year) the lead returns to "new"
    rented_at = Column(DateTime(timezone=True), nullable=True)

    # Notification tracking
    notified = Column(Boolean, default=False)
    notified_at = Column(DateTime(timezone=True))
    notification_channel = Column(String(50))  # telegram | email | none

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    property = relationship("Property", backref="leads")

    def __repr__(self):
        return f"<Lead(id={self.id}, phone={self.phone_number}, status={self.status})>"

    def to_dict(self):
        return {
            "id": self.id,
            "property_id": self.property_id,
            "phone_number": self.phone_number,
            "seller_name": self.seller_name,
            "city_name": self.city_name,
            "category_name": self.category_name,
            "listing_type": self.listing_type,
            "price": self.price,
            "area": self.area,
            "property_url": self.property_url,
            "property_title": self.property_title,
            "status": self.status,
            "notes": self.notes,
            "assigned_to": self.assigned_to,
            "rented_at": self.rented_at.isoformat() if self.rented_at else None,
            "notified": self.notified,
            "notified_at": self.notified_at.isoformat() if self.notified_at else None,
            "notification_channel": self.notification_channel,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
