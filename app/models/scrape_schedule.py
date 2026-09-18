"""
A scrape that runs itself: the same form, every day at the same hour.

Every run so far was somebody pressing «شروع». Between 15 and 18 September
that happened once — the tool only produces leads on the days somebody
remembers it. A schedule is the saved form plus an hour, owned by the person
who saved it, so the run uses their Divar accounts and nobody else's — the
same rule a manual run follows.
"""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.sql import func

from app.database import Base


class ScrapeSchedule(Base):
    __tablename__ = "scrape_schedules"

    id = Column(Integer, primary_key=True, index=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    # The scrape form as submitted: a ScrapingJobCreate dict. Kept whole so a
    # schedule launches exactly what the form would have.
    config = Column(JSON, nullable=False)
    # Tehran wall-clock time. Stored as hour + minute rather than a cron
    # string: the panel offers a time picker, not a cron editor.
    hour = Column(Integer, nullable=False, default=8)
    minute = Column(Integer, nullable=False, default=0)
    enabled = Column(Boolean, nullable=False, default=True)

    next_run_at = Column(DateTime(timezone=True), index=True)
    last_run_at = Column(DateTime(timezone=True))
    last_job_id = Column(String(40))
    # {"status": "started"|"skipped"|"failed", "detail": "..."} — what
    # happened at the last firing, shown on the card.
    last_result = Column(JSON)
    note = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "owner_user_id": self.owner_user_id,
            "name": self.name,
            "config": self.config or {},
            "hour": self.hour,
            "minute": self.minute,
            "enabled": bool(self.enabled),
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_job_id": self.last_job_id,
            "last_result": self.last_result or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
