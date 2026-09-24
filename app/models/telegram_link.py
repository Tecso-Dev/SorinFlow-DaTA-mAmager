"""
اتصال به تلگرام — which Telegram account is which panel user.

«سورین» answers a person, not a chat: the Telegram account that writes to
the bot is looked up here, and the panel user it belongs to decides what
the assistant may read for them (app/ai/assistant.py). One account per
user and one user per account; a new link replaces both sides' old ones.

Made by a one-time code the user takes from their own profile and sends to
the bot in a private chat: the code proves the panel user, Telegram proves
the Telegram user.
"""
from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.sql import func
from app.database import Base


class TelegramLink(Base):
    __tablename__ = "telegram_links"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    telegram_user_id = Column(BigInteger, nullable=False, unique=True)   # Telegram's from.id
    telegram_username = Column(String(64))                               # @name, if the account has one
    linked_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def to_dict(self):
        return {
            "linked": True, "telegram_username": self.telegram_username,
            "linked_at": self.linked_at.isoformat() if self.linked_at else None,
        }
