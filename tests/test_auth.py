"""
Tests for app/auth/jwt.py — password hashing and JWT utilities.
No database or async needed.
"""
import time
import pytest
from app.auth.jwt import (
    verify_password,
    get_password_hash,
    create_access_token,
    decode_token,
)
from jose import JWTError


class TestPasswordHashing:
    def test_hash_is_not_plaintext(self):
        hashed = get_password_hash("mysecret")
        assert hashed != "mysecret"

    def test_verify_correct_password(self):
        hashed = get_password_hash("correct_password")
        assert verify_password("correct_password", hashed) is True

    def test_verify_wrong_password(self):
        hashed = get_password_hash("correct_password")
        assert verify_password("wrong_password", hashed) is False

    def test_same_password_different_hashes(self):
        h1 = get_password_hash("same")
        h2 = get_password_hash("same")
        assert h1 != h2  # bcrypt salts differ

    def test_empty_password_hashes(self):
        hashed = get_password_hash("")
        assert verify_password("", hashed) is True

    def test_unicode_password(self):
        hashed = get_password_hash("پسورد۱۲۳")
        assert verify_password("پسورد۱۲۳", hashed) is True
        assert verify_password("wrong", hashed) is False


class TestJWT:
    def test_create_and_decode(self):
        token = create_access_token({"sub": "testuser", "role": "admin"})
        payload = decode_token(token)
        assert payload["sub"] == "testuser"
        assert payload["role"] == "admin"

    def test_token_has_expiry(self):
        token = create_access_token({"sub": "u"})
        payload = decode_token(token)
        assert "exp" in payload

    def test_expired_token_raises(self):
        token = create_access_token({"sub": "u"}, expires_minutes=-1)
        with pytest.raises(JWTError):
            decode_token(token)

    def test_tampered_token_raises(self):
        token = create_access_token({"sub": "u"})
        tampered = token[:-4] + "XXXX"
        with pytest.raises(JWTError):
            decode_token(tampered)

    def test_short_lived_totp_session(self):
        token = create_access_token({"sub": "u", "totp_pending": True}, expires_minutes=5)
        payload = decode_token(token)
        assert payload.get("totp_pending") is True

    def test_custom_expiry(self):
        before = time.time()
        token = create_access_token({"sub": "u"}, expires_minutes=30)
        payload = decode_token(token)
        # exp should be ~30 min from now
        assert payload["exp"] > before + 29 * 60
        assert payload["exp"] < before + 31 * 60
