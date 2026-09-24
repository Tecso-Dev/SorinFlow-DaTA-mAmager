"""
SorinFlow Divar Scraper - Cookie Model
"""
from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, JSON, ForeignKey
from sqlalchemy.sql import func, true as sql_true
from app.database import Base


class Cookie(Base):
    """Cookie model for storing authentication cookies"""
    __tablename__ = "cookies"
    
    id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String(20), nullable=False, index=True)
    # Whose Divar number this is.
    #
    # «i am sobhan and my number is 09058432452 so i should use that number …
    # and i can acces only to my account numbers and not other account».
    # Nullable only so the column can be added to a live table; the backfill
    # beside the migration gives every existing row an owner, and nothing
    # creates one without.
    owner_user_id = Column(Integer, ForeignKey("users.id", name="fk_cookies_owner", ondelete="SET NULL"), index=True)
    cookies = Column(JSON, nullable=False)  # Store all cookies as JSON
    token = Column(Text)  # JWT token if extracted
    is_valid = Column(Boolean, default=True)
    expires_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # ── چرخش شماره ───────────────────────────────────────────────────────
    # Divar's SMS challenge is charged to the *account*, and it does not forget
    # between our scraping jobs. Counting reveals on the scraper object could
    # not work: a new one is built per job, so the count restarted while the
    # account's real spend kept climbing. It belongs here, next to the session
    # it applies to.
    reveals = Column(Integer, default=0, nullable=False)
    last_used_at = Column(DateTime(timezone=True))

    # When Divar last actually answered about this session. Distinct from
    # updated_at, which moves on any write: a row saved an hour ago is not a
    # session verified an hour ago, and the panel used to present the two as
    # the same thing.
    last_checked_at = Column(DateTime(timezone=True))
    # When Divar last asked this account for a code. A heavily used account
    # that keeps being challenged is Divar saying «not this one, not today»;
    # rotation rests it rather than handing it back every cycle.
    challenged_at = Column(DateTime(timezone=True))
    # When Divar asked this account to prove who it is — national ID, birth
    # date. Unlike a code challenge this does not pass with time or with a
    # human typing six digits: somebody has to log in on Divar and do it.
    # Rotation skips the account while this is set; the panel clears it.
    identity_required_at = Column(DateTime(timezone=True))
    # The owner's own switch: «this number is not reachable right now».
    #
    # Every other flag here is Divar's verdict about the session. This one is
    # a person's: the SIM is in a drawer, the phone is off, the line is being
    # moved. Rotation that lands on such a number gets a code sent to a phone
    # nobody can read, and a run parks for hours waiting on it. Off means
    # rotation, «خودکار» and a manual pick all pass it by; the session itself
    # is kept, so switching it back on costs nothing.
    is_enabled = Column(Boolean, default=True, server_default=sql_true(), nullable=False)

    def __repr__(self):
        return f"<Cookie(id={self.id}, phone={self.phone_number}, valid={self.is_valid})>"
    
    def to_dict(self):
        """Convert cookie to dictionary"""
        return {
            "id": self.id,
            "phone_number": self.phone_number,
            "is_valid": self.is_valid,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "reveals": self.reveals or 0,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "last_checked_at": self.last_checked_at.isoformat() if self.last_checked_at else None,
            "owner_user_id": self.owner_user_id,
            "identity_required_at": self.identity_required_at.isoformat() if self.identity_required_at else None,
            "is_enabled": self.is_enabled is not False,
        }
