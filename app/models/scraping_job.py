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
    status = Column(String(50), default="pending")  # pending, running, paused, completed, failed, cancelled
    
    # Progress
    total_pages = Column(Integer, default=0)
    scraped_pages = Column(Integer, default=0)
    total_items = Column(Integer, default=0)
    scraped_items = Column(Integer, default=0)
    new_items = Column(Integer, default=0)
    updated_items = Column(Integer, default=0)
    failed_items = Column(Integer, default=0)
    
    # Divar session used for this job
    divar_phone = Column(String(20), nullable=True)

    # Error handling
    error_message = Column(Text)

    # Why the run stopped, when it stopped short of the requested number.
    # Finishing at 42% and saying only «completed» is indistinguishable from a
    # fault, so a run that ran out of listings has to say that it did. Not an
    # error — a completed job with nothing wrong still fills this in.
    finish_reason = Column(String(300))

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


class SkippedListing(Base):
    """A candidate a run saw and did not save, kept so it can be retried.

    Every run drops listings for reasons that are usually correct — a filter
    said no, the category did not match, the page would not open — and until
    now the only trace was a number in the finish line. «۳۲ خارج از دسته‌بندی»
    is not something anyone can check or act on: the listings themselves were
    gone.

    One row per drop, with the Divar link, so the panel can show them and a
    single scrape can pick one up afterwards.
    """
    __tablename__ = "skipped_listings"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("scraping_jobs.job_id"), index=True)
    divar_id = Column(String(32), index=True)
    url = Column(String(400))
    title = Column(String(300))
    # The tally bucket the run counted it under — «category», «deposit»,
    # «failed» — so the panel can name it with the labels it already has.
    reason = Column(String(64), index=True)
    # The specific one, where there is a specific one: «صفحه باز نشد», or the
    # filter's own words.
    detail = Column(String(300))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<SkippedListing({self.divar_id} {self.reason})>"
