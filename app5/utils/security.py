"""Password hashing and lightweight session/token helpers."""
from __future__ import annotations

import secrets

import bcrypt


def hash_password(plain_password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(plain_password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def generate_session_token() -> str:
    """Generate a random, URL-safe session token."""
    return secrets.token_urlsafe(32)


def is_strong_password(password: str) -> tuple[bool, str]:
    """Basic password policy check. Returns (ok, reason)."""
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not any(c.isupper() for c in password):
        return False, "Password must include at least one uppercase letter."
    if not any(c.isdigit() for c in password):
        return False, "Password must include at least one digit."
    return True, ""
