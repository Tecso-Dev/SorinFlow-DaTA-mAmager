"""
Small key/value store for settings that must outlive a pod.

Maintenance mode is the first of them: it can stay on for days, across
deploys and restarts, so it cannot live in a module-level variable.
"""
from sqlalchemy import Column, String, Text, DateTime
from sqlalchemy.sql import func

from app.database import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text)
    updated_by = Column(String(200))
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def to_dict(self):
        return {
            "key": self.key,
            "value": self.value,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
