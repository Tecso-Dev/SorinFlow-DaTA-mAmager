"""
SorinFlow — Dashboard User Model
Roles: root | super_admin | admin | visitor  (see app/auth/permissions.py)
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, JSON, Text
from sqlalchemy.sql import func
from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(200), unique=True, nullable=True)
    full_name = Column(String(200))
    hashed_password = Column(String(500), nullable=False)
    role = Column(String(50), nullable=False, default="visitor")
    # allowed: root | super_admin | admin | visitor
    is_active = Column(Boolean, default=True)
    last_login = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    # Divar account linked to this user (staff only — unrelated to `phone`)
    divar_phone = Column(String(20), nullable=True)
    # Google Authenticator 2FA
    totp_secret = Column(String(64), nullable=True)
    totp_enabled = Column(Boolean, default=False, nullable=False)
    # A second factor that needs no app, no QR and no clock.
    #
    # TOTP asks someone to scan a code, keep a phone in sync and never lose the
    # entry — and when any of that fails there is no way back in except the
    # database. Email costs a round trip and recovers itself.
    email_2fa_enabled = Column(Boolean, default=False, nullable=False)

    # ── Portal sign-up ────────────────────────────────────────────────────
    # The phone that receives the login code. Separate from divar_phone on
    # purpose: one is who you are here, the other is which Divar account you
    # scrape with, and conflating them would let a staff member's scraper
    # account double as a login identity.
    phone = Column(String(20), unique=True, nullable=True, index=True)
    phone_verified = Column(Boolean, default=False, nullable=False)
    # Set only by a code that arrived at the address. Kept apart from
    # phone_verified on purpose: while there is no SMS provider every code
    # travels by email, and collapsing the two would have the panel promise a
    # reachable number to whoever is about to dial it.
    email_verified = Column(Boolean, default=False, nullable=False)
    # Email is collected for marketing, never used as the verification gate —
    # delivery from Iran is not reliable enough to stand between a user and
    # their account. Only set when the user actually ticked the box.
    marketing_opt_in = Column(Boolean, default=False, nullable=False)
    # Which areas an admin may reach. Ignored for every other role: root and
    # super_admin bypass, visitors are refused before it is read.
    permissions = Column(JSON, default=list)

    # ── profile ──
    headline = Column(String(120))          # «مشاور فروش»، shown under the name
    bio = Column(Text)
    links = Column(JSON, default=dict)      # {"website", "instagram", "linkedin"}
    # A label the person sets, not a measurement: nothing here tracks presence.
    presence = Column(String(16), default="available", nullable=False)
    # The avatar file is images/avatars/<token>.jpg. A random token rather than
    # the user id, so the URL is not guessable and changes on every upload —
    # which is also what makes a browser drop the cached old picture.
    avatar_token = Column(String(32))
    # Bumped when the password changes. A token carrying an older number is
    # refused, which is how «sign out my other devices» works without a
    # revocation list.
    token_version = Column(Integer, default=0, nullable=False)

    @property
    def avatar_url(self):
        return f"/images/avatars/{self.avatar_token}.jpg" if self.avatar_token else None

    def __repr__(self):
        return f"<User(id={self.id}, username={self.username}, role={self.role})>"
