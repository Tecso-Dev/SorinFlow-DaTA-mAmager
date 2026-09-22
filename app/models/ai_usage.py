"""
هوش مصنوعی — the ledger.

Every call to the model gateway leaves one row: which agent asked, which
model answered, what it cost. Liara returns the cost with every response, so
the figures are its own, not an estimate. The panel's AI card reads today's
and this month's totals from here, and the daily cap is enforced on it —
the one number that decides whether the next background call may go out.
"""
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime
from sqlalchemy.sql import func
from app.database import Base


class AiUsage(Base):
    __tablename__ = "ai_usage"

    id = Column(Integer, primary_key=True, index=True)
    agent = Column(String(40), nullable=False, index=True)    # explainer | reader | need | embed | vision | assistant | test
    job = Column(String(20), nullable=False)                  # write | read | vision | embed
    model = Column(String(120))
    prompt_tokens = Column(Integer, default=0, nullable=False)
    completion_tokens = Column(Integer, default=0, nullable=False)
    cost_usd = Column(Float, default=0.0, nullable=False)
    cost_toman = Column(Float, default=0.0, nullable=False)
    ms = Column(Integer, default=0, nullable=False)
    ok = Column(Boolean, default=True, nullable=False)
    error = Column(String(300))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    def to_dict(self):
        return {
            "id": self.id, "agent": self.agent, "job": self.job, "model": self.model,
            "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
            "cost_usd": self.cost_usd, "cost_toman": self.cost_toman, "ms": self.ms,
            "ok": self.ok, "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
