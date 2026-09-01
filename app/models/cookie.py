"""
SorinFlow Divar Scraper - Cookie Model
"""
from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, JSON
from sqlalchemy.sql import func
from app.database import Base


class Cookie(Base):
    """Cookie model for storing authentication cookies"""
    __tablename__ = "cookies"
    
    id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String(20), nullable=False, index=True)
    cookies = Column(JSON, nullable=False)  # Store all cookies as JSON
    token = Column(Text)  # JWT token if extracted
    is_valid = Column(Boolean, default=True)
    expires_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # ── چرخش شماره ───────────────────────────────────────────────────────
    # Divar's SMS challenge is charged to the *account*, and it does not forget
    # between our scraping jobs. Counting reveals on the scraper object could
    # not work: a new one is built per job, so the count restarted while the
    # account's real spend kept climbing. It belongs here, next to the session
    # it applies to.
    reveals = Column(Integer, default=0, nullable=False)
    last_used_at = Column(DateTime(timezone=True))

    # When Divar last actually answered about this session. Distinct from
    # updated_at, which moves on any write: a row saved an hour ago is not a
    # session verified an hour ago, and the panel used to present the two as
    # the same thing.
    last_checked_at = Column(DateTime(timezone=True))
    
    def __repr__(self):
        return f"<Cookie(id={self.id}, phone={self.phone_number}, valid={self.is_valid})>"
    
    def to_dict(self):
        """Convert cookie to dictionary"""
        return {
            "id": self.id,
            "phone_number": self.phone_number,
            "is_valid": self.is_valid,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "reveals": self.reveals or 0,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "last_checked_at": self.last_checked_at.isoformat() if self.last_checked_at else None,
        }
