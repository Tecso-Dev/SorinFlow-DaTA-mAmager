"""
audit_events — چه کسی، چه کاری، کِی. Staff login, password/2FA changes, user
management, Divar number ownership, backups and provider-settings saves.
Written by app/services/audit.py; read by app/api/routes/audit.py, root and
super_admin only.

No FK on actor_user_id: users get deleted and this is a snapshot of who did
it, not a live pointer that would go dangling or cascade-delete history —
actor_username and actor_role are copied in at write time for the same
reason. `detail` never carries secrets (audit.py strips them recursively
before a row is written).

Indexes are plain and unnamed beyond their own columns on purpose — multi-
agency readiness (roadmap): an agency_id column can be added to any of them
later without restructuring.
"""
from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, JSON, String
from sqlalchemy.sql import func

from app.database import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(BigInteger, primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    actor_user_id = Column(Integer, nullable=True)
    actor_username = Column(String(100))
    actor_role = Column(String(20))

    action = Column(String(64), nullable=False)
    target_type = Column(String(40))
    target_id = Column(String(64))
    summary = Column(String(300))       # Persian, human-readable
    detail = Column(JSON)

    ip = Column(String(64))
    request_id = Column(String(64))

    __table_args__ = (
        Index("ix_audit_events_created_at", "created_at"),
        Index("ix_audit_events_actor_created", "actor_user_id", "created_at"),
        Index("ix_audit_events_action_created", "action", "created_at"),
    )
