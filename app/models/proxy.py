"""
SorinFlow Divar Scraper - Proxy Model
"""
from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime
from sqlalchemy.sql import func
from app.database import Base


class Proxy(Base):
    """Proxy model for storing proxy server information"""
    __tablename__ = "proxies"
    
    id = Column(Integer, primary_key=True, index=True)
    address = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False)
    username = Column(String(100))
    password = Column(String(100))
    protocol = Column(String(20), default="http")  # http, https, socks5
    
    # Status
    is_active = Column(Boolean, default=True)
    is_working = Column(Boolean, default=True)
    
    # Statistics
    last_checked = Column(DateTime(timezone=True))
    fail_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    avg_response_time = Column(Float)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    def __repr__(self):
        return f"<Proxy(id={self.id}, address={self.address}:{self.port}, working={self.is_working})>"
    
    @property
    def url(self) -> str:
        """Get proxy URL"""
        if self.username and self.password:
            return f"{self.protocol}://{self.username}:{self.password}@{self.address}:{self.port}"
        return f"{self.protocol}://{self.address}:{self.port}"
    
    def to_dict(self):
        """Convert proxy to dictionary"""
        return {
            "id": self.id,
            "address": self.address,
            "port": self.port,
            "protocol": self.protocol,
            "is_active": self.is_active,
            "is_working": self.is_working,
            "fail_count": self.fail_count,
            "success_count": self.success_count,
            "avg_response_time": self.avg_response_time,
            "last_checked": self.last_checked.isoformat() if self.last_checked else None,
        }
