"""
A phone that forwards Divar's SMS codes, and who it belongs to.

Until now there was one OTP_INBOUND_SECRET for the whole installation. That
works for one operator with one phone and fails the moment a second person
joins: they would have to be handed the same secret, every phone could answer
for every account, and revoking one device means rotating the secret for
everybody.

A device row is one phone. It carries its own secret, so a phone can be added
or revoked on its own; it belongs to a user, so a code it forwards can only
answer a Divar account that user owns; and it records what the heartbeat last
said, so the panel can tell «online» from «configured but silent» — the
difference between nothing to do and go and look at your phone.
"""
import secrets

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer,
                        String, Text)
from sqlalchemy.sql import func

from app.database import Base


def new_secret() -> str:
    """32 bytes of hex. Long enough that brute force is not the weak point,
    short enough to read off a screen and type into a phone once."""
    return secrets.token_hex(32)


def new_device_id() -> str:
    """The public handle the phone sends in X-Forwarder-Id.

    Not the row id: the phone puts this in a header on every request, and a
    guessable integer would let anyone probe whether device 5 exists. Not the
    secret either — this one is allowed to be seen.
    """
    return secrets.token_hex(8)


class ForwarderDevice(Base):
    __tablename__ = "forwarder_devices"

    id = Column(Integer, primary_key=True, index=True)
    # What the phone sends; what the server looks the secret up by.
    device_id = Column(String(32), unique=True, nullable=False, index=True,
                       default=new_device_id)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # Shown once on creation and thereafter on demand to the owner only. Stored
    # in the clear on purpose: the panel has to be able to show it again when
    # somebody reinstalls the app, and a one-way hash cannot do that. It is
    # scoped to one device and one user's accounts, and rotating it is a click.
    secret = Column(String(64), nullable=False, default=new_secret)

    label = Column(String(80))                    # «شیائومی سبحان»
    # The SIM in this phone, so the panel can say which Divar account it
    # answers for. Informational: authority comes from user_id.
    sim_phone = Column(String(20), index=True)

    is_active = Column(Boolean, default=True, nullable=False)

    # Last heartbeat, and what it carried.
    last_seen_at = Column(DateTime(timezone=True), index=True)
    battery = Column(Integer)
    network = Column(String(24))
    app_version = Column(String(24))

    # Last real code, which is the only proof the whole path works. A device
    # can heartbeat happily and still never deliver — a filter typo does
    # exactly that — so «last seen» and «last delivered» are separate facts.
    last_code_at = Column(DateTime(timezone=True))
    codes_forwarded = Column(Integer, default=0, nullable=False)

    # Set when a delivery problem has been emailed, so the owner is told once
    # rather than every five minutes.
    warned_at = Column(DateTime(timezone=True))

    note = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def masked_secret(self) -> str:
        s = self.secret or ""
        return f"{s[:6]}…{s[-4:]}" if len(s) > 12 else "—"

    def to_dict(self, *, reveal_secret: bool = False) -> dict:
        return {
            "id": self.id,
            "device_id": self.device_id,
            "user_id": self.user_id,
            "label": self.label,
            "sim_phone": self.sim_phone,
            "is_active": self.is_active,
            "secret": self.secret if reveal_secret else None,
            "secret_masked": self.masked_secret(),
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "last_code_at": self.last_code_at.isoformat() if self.last_code_at else None,
            "codes_forwarded": self.codes_forwarded or 0,
            "battery": self.battery,
            "network": self.network,
            "app_version": self.app_version,
            "note": self.note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
