"""
SorinFlow Divar Scraper - Scraping Job Model
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
from app.database import Base


class ScrapingJob(Base):
    """Scraping job model for tracking scraping tasks"""
    __tablename__ = "scraping_jobs"
    
    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(UUID(as_uuid=True), default=uuid.uuid4, unique=True, nullable=False, index=True)
    city_id = Column(Integer, ForeignKey("cities.id"))
    category_id = Column(Integer, ForeignKey("categories.id"))
    
    # Status
    status = Column(String(50), default="pending")  # pending, running, completed, failed, cancelled
    
    # Progress
    total_pages = Column(Integer, default=0)
    scraped_pages = Column(Integer, default=0)
    total_items = Column(Integer, default=0)
    scraped_items = Column(Integer, default=0)
    new_items = Column(Integer, default=0)
    updated_items = Column(Integer, default=0)
    failed_items = Column(Integer, default=0)
    
    # Error handling
    error_message = Column(Text)
    
    # Timestamps
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    logs = relationship("ScrapingLog", back_populates="job", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<ScrapingJob(id={self.id}, job_id={self.job_id}, status={self.status})>"
    
    def to_dict(self):
        """Convert job to dictionary"""
        return {
            "id": self.id,
            "job_id": str(self.job_id),
            "city_id": self.city_id,
            "category_id": self.category_id,
            "status": self.status,
            "total_pages": self.total_pages,
            "scraped_pages": self.scraped_pages,
            "total_items": self.total_items,
            "scraped_items": self.scraped_items,
            "new_items": self.new_items,
            "updated_items": self.updated_items,
            "failed_items": self.failed_items,
            "error_message": self.error_message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "progress": self.progress
        }
    
    @property
    def progress(self) -> float:
        """Calculate progress percentage"""
        if self.total_items == 0:
            return 0.0
        return round((self.scraped_items / self.total_items) * 100, 2)


class ScrapingLog(Base):
    """Scraping log model for tracking scraping events"""
    __tablename__ = "scraping_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("scraping_jobs.job_id"))
    level = Column(String(20))  # debug, info, warning, error
    message = Column(Text)
    details = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relationships
    job = relationship("ScrapingJob", back_populates="logs")
    
    def __repr__(self):
        return f"<ScrapingLog(id={self.id}, level={self.level}, message={self.message[:30]}...)>"
