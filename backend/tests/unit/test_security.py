from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core import security
from app.core.config import get_settings


def test_password_hash_roundtrip():
    hashed = security.hash_password("S3cret!pass")
    assert hashed != "S3cret!pass" and hashed.startswith("$2")
    assert security.verify_password("S3cret!pass", hashed)
    assert not security.verify_password("wrong", hashed)


def test_same_password_gets_different_hashes():
    assert security.hash_password("abc12345") != security.hash_password("abc12345")


def test_verify_with_malformed_hash_is_false_not_error():
    assert security.verify_password("x", "not-a-bcrypt-hash") is False


def test_token_roundtrip_and_expiry_is_8_hours_by_default():
    token, expires_in = security.create_access_token(7, "priya.sharma", "TRADER")
    payload = security.decode_access_token(token)
    assert payload["sub"] == "7" and payload["username"] == "priya.sharma" and payload["role"] == "TRADER"
    assert expires_in == get_settings().jwt_expire_minutes * 60


def test_expired_token_rejected():
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    token = jwt.encode({"sub": "1", "exp": past}, get_settings().jwt_secret, algorithm="HS256")
    with pytest.raises(jwt.ExpiredSignatureError):
        security.decode_access_token(token)


def test_token_signed_with_other_secret_rejected():
    token = jwt.encode({"sub": "1"}, "x" * 40, algorithm="HS256")
    with pytest.raises(jwt.InvalidSignatureError):
        security.decode_access_token(token)
