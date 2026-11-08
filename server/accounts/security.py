"""Password hashing and opaque tokens.

Session cookies, API tokens and password links are random strings handed to the
client once; only their SHA-256 is stored, so a database leak can't be replayed.
"""

import hashlib
import re
import secrets
from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()

# Verified against when the email is unknown, so a miss costs as much as a hit.
_DUMMY_HASH = _hasher.hash("mimikiku-dummy-password")

MIN_PASSWORD = 8
MAX_PASSWORD = 256
API_TOKEN_PREFIX = "mk_"

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+$")


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(nbytes: int = 9) -> str:
    """URL-safe random id (12 chars for the default 9 bytes)."""
    return secrets.token_urlsafe(nbytes)


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def new_api_token() -> str:
    return API_TOKEN_PREFIX + secrets.token_urlsafe(32)


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_problem(password: str) -> str | None:
    """A user-facing reason the password is unacceptable, or None."""
    if len(password) < MIN_PASSWORD:
        return f"Password must be at least {MIN_PASSWORD} characters"
    if len(password) > MAX_PASSWORD:
        return f"Password must be at most {MAX_PASSWORD} characters"
    return None


def normalize_email(email: str) -> str | None:
    email = email.strip().lower()
    return email if _EMAIL.match(email) and len(email) <= 254 else None
