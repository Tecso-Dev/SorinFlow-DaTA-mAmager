"""
What the SMS panel did, kept where it can still be read afterwards.

`crm_sms_logs` records messages that were *sent* — one row per recipient. It
cannot answer the questions that actually get asked when SMS stops working:

  * «چرا پیامک نرفت؟» — the failure is a status code inside a response blob,
    and the reason it happened (no template, an unusable sender line) is not
    written down anywhere.
  * «کی تنظیمات را عوض کرد؟» — nothing recorded a settings change at all, so a
    panel that worked yesterday and not today had no history to look at.
  * «الگو کِی تأیید شد؟» — the moment a Kavenegar template goes from «در حال
    بررسی» to working is invisible; it shows up only as sends starting to
    succeed.

Those are events about the *service*, not about a message, which is why they do
not belong in crm_sms_logs. One row per thing that happened.
"""
from sqlalchemy import Column, DateTime, Integer, String, Text, Index
from sqlalchemy.sql import func

from app.database import Base


class SmsEvent(Base):
    __tablename__ = "sms_events"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    # A small, fixed vocabulary — see app/services/sms_log.py. A stage list
    # nobody can remember gets used inconsistently and then means nothing.
    stage = Column(String(24), nullable=False, index=True)
    level = Column(String(10), nullable=False, default="info", index=True)

    message = Column(Text, nullable=False)

    # Which Kavenegar route was involved, when one was: "verify" or "sms".
    route = Column(String(16), nullable=True)
    # Kavenegar's own status, when the event came from a call.
    status = Column(Integer, nullable=True)
    # Who caused it. Empty for anything the system did on its own.
    actor = Column(String(200), nullable=True)

    details = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_sms_events_created_stage", "created_at", "stage"),
    )
