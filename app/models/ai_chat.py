"""
دستیار دفتر — the questions.

Every question the assistant (app/ai/assistant.py) answered, with who asked,
which tools it read and what it said back — so the panel can show the
office what its own assistant is being asked, and a wrong answer can be
traced to the tool that fed it.
"""
from sqlalchemy import Column, Integer, String, Text, Boolean, JSON, DateTime
from sqlalchemy.sql import func
from app.database import Base


class AiChat(Base):
    __tablename__ = "ai_chats"

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(String(40), index=True)       # the Telegram chat, or "" from the panel
    who = Column(String(120))                      # the name Telegram gave, or the panel user
    question = Column(Text, nullable=False)
    answer = Column(Text)
    tools = Column(JSON)                           # the tool names, in the order they were read
    ms = Column(Integer, default=0, nullable=False)
    ok = Column(Boolean, default=True, nullable=False)
    error = Column(String(300))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    def to_dict(self):
        return {
            "id": self.id, "chat_id": self.chat_id, "who": self.who,
            "question": self.question, "answer": self.answer, "tools": self.tools or [],
            "ms": self.ms, "ok": self.ok, "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
