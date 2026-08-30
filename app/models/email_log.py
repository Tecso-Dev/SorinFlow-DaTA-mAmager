"""
A record of every email the system sends.

Separate from crm_sms_logs rather than sharing it: an email has a subject, a
template name and a recipient address, and an SMS has none of those. Forcing
both into one table would mean half the columns are null in every row and the
panel has to know which half.

One-time codes are logged without their body — the row proves the send
happened and to whom, which is what "did they get their code?" needs, while the
code itself is a credential and does not belong in a table the panel lists.
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, Index
from sqlalchemy.sql import func

from app.database import Base


class EmailLog(Base):
    __tablename__ = "email_logs"

    id = Column(Integer, primary_key=True, index=True)

    to_email = Column(String(255), nullable=False, index=True)
    subject = Column(String(300))
    template = Column(String(60), index=True)     # login_code | welcome | ...

    status = Column(String(20), default="sent", index=True)   # sent | failed
    error = Column(Text)
    message_id = Column(String(255))

    sent_by = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "to_email": self.to_email,
            "subject": self.subject,
            "template": self.template,
            "status": self.status,
            "error": self.error,
            "message_id": self.message_id,
            "sent_by": self.sent_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# The panel lists newest-first, optionally filtered by template.
Index("ix_email_logs_template_created", EmailLog.template, EmailLog.created_at.desc())
