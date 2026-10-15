"""Password hashing (bcrypt) and JWT access tokens (HS256)."""
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import get_settings

ALGORITHM = "HS256"
_BCRYPT_MAX_BYTES = 72  # bcrypt ignores anything after 72 bytes


def _to_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_to_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_to_bytes(password), password_hash.encode("utf-8"))
    except ValueError:  # malformed hash in the DB
        return False


# Used when the username does not exist, so a wrong username takes as long as a wrong password.
DUMMY_HASH = hash_password("not-a-real-password")


def create_access_token(user_id: int, username: str, role: str) -> tuple[str, int]:
    """Return (token, seconds until expiry)."""
    settings = get_settings()
    expires_in = settings.jwt_expire_minutes * 60
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM), expires_in


def decode_access_token(token: str) -> dict:
    """Raises jwt.PyJWTError if the token is invalid or expired."""
    return jwt.decode(token, get_settings().jwt_secret, algorithms=[ALGORITHM])
