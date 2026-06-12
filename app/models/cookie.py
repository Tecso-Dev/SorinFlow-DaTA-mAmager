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
        }
