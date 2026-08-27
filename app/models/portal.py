"""
SorinFlow — visitor portal tables.

A visitor never sees the dashboard. They get two things: a form describing the
property they are looking for, and a ticket asking to be upgraded to admin.
Both land here for super_admin to work through.
"""
from sqlalchemy import (
    Column, Integer, BigInteger, String, Boolean, DateTime, Text, JSON, ForeignKey, Index
)
from sqlalchemy.sql import func
from app.database import Base


class PropertyRequest(Base):
    """«دنبال چه ملکی هستید؟» — a visitor's stated requirement.

    Deliberately stored as plain columns rather than a blob: the matcher in
    app/services/match_service.py already scores customers on these same
    fields, so a later phase can feed a request straight into it without a
    translation layer.
    """
    __tablename__ = "portal_property_requests"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # What they want
    deal_type = Column(String(10), default="buy")        # buy | rent
    property_kind = Column(String(30))                   # apartment | villa | land | office | store
    city = Column(String(100))
    districts = Column(String(500))                      # free text, comma separated

    # Money (Toman). Rent deals use deposit_max/rent_max, buy uses budget_*.
    budget_min = Column(BigInteger)
    budget_max = Column(BigInteger)
    deposit_max = Column(BigInteger)
    rent_max = Column(BigInteger)

    # Shape
    area_min = Column(Integer)
    area_max = Column(Integer)
    rooms_min = Column(Integer)
    year_built_min = Column(Integer)
    needs_elevator = Column(Boolean, default=False)
    needs_parking = Column(Boolean, default=False)
    needs_storage = Column(Boolean, default=False)

    description = Column(Text)
    contact_name = Column(String(200))
    contact_phone = Column(String(20))

    # Workflow
    status = Column(String(20), default="new", index=True)  # new|in_review|matched|contacted|closed
    admin_note = Column(Text)
    matched_property_id = Column(Integer, nullable=True)
    handled_by = Column(String(100))

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "deal_type": self.deal_type,
            "property_kind": self.property_kind,
            "city": self.city,
            "districts": self.districts,
            "budget_min": self.budget_min,
            "budget_max": self.budget_max,
            "deposit_max": self.deposit_max,
            "rent_max": self.rent_max,
            "area_min": self.area_min,
            "area_max": self.area_max,
            "rooms_min": self.rooms_min,
            "year_built_min": self.year_built_min,
            "needs_elevator": bool(self.needs_elevator),
            "needs_parking": bool(self.needs_parking),
            "needs_storage": bool(self.needs_storage),
            "description": self.description,
            "contact_name": self.contact_name,
            "contact_phone": self.contact_phone,
            "status": self.status,
            "admin_note": self.admin_note,
            "matched_property_id": self.matched_property_id,
            "handled_by": self.handled_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class UpgradeTicket(Base):
    """A visitor asking super_admin to make them an admin.

    granted_permissions records what was actually handed over at approval time,
    so a later change to a user's toggles does not rewrite the history of what
    was agreed.
    """
    __tablename__ = "portal_upgrade_tickets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    message = Column(Text)
    contact_phone = Column(String(20))

    status = Column(String(20), default="pending", index=True)  # pending|approved|rejected
    decision_note = Column(Text)
    decided_by = Column(String(100))
    decided_at = Column(DateTime(timezone=True))
    granted_permissions = Column(JSON, default=list)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "message": self.message,
            "contact_phone": self.contact_phone,
            "status": self.status,
            "decision_note": self.decision_note,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "granted_permissions": self.granted_permissions or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


Index("ix_portal_requests_status_created", PropertyRequest.status, PropertyRequest.created_at)
