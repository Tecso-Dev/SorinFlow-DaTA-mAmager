"""
SorinFlow Divar Scraper - CRM Lead Model
"""
from sqlalchemy import Column, Integer, String, BigInteger, Boolean, Text, ForeignKey, DateTime, event
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from app.models.phone import sync_phone_columns


class Lead(Base):
    """CRM lead created from a scraped property"""
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False, index=True)

    # Contact info (copied from property for quick access)
    phone_number = Column(String(20), index=True)
    # Kept in sync by the before_insert/before_update listener below —
    # +98/0098/98/0 and Persian digits all collapse to the same string, so a
    # dedupe check does not miss a lead just because the number was typed
    # differently the second time. See app/models/phone.py.
    phone_number_normalized = Column(String(20), index=True)
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

    # The call queue. A lead is «due» when next_call_at is empty (never
    # called, or answered) or has passed (a callback, an unanswered retry).
    # call_attempts counts every dial, whatever the outcome.
    next_call_at = Column(DateTime(timezone=True), nullable=True, index=True)
    call_attempts = Column(Integer, default=0, nullable=False)
    last_call_at = Column(DateTime(timezone=True), nullable=True)
    last_call_outcome = Column(String(20), nullable=True)

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


_sync_lead_phone = sync_phone_columns(("phone_number", "phone_number_normalized"))
event.listen(Lead, "before_insert", _sync_lead_phone)
event.listen(Lead, "before_update", _sync_lead_phone)
