"""Security utilities for password hashing, JWT token management, and token generation."""

import secrets
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from backend.app.config import settings

# Password hashing context with bcrypt, cost factor 12
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)


# --- Password Hashing ---


def hash_password(password: str) -> str:
    """Hash a password using bcrypt with cost factor 12."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)


# --- JWT Token Generation / Validation ---


def create_access_token(subject: str | int, extra_claims: dict | None = None) -> str:
    """Create a short-lived JWT access token.

    Args:
        subject: The token subject (typically user ID or email).
        extra_claims: Optional additional claims to include in the token.

    Returns:
        Encoded JWT access token string.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload = {
        "sub": str(subject),
        "exp": expire,
        "type": "access",
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: str | int) -> str:
    """Create a long-lived JWT refresh token.

    Args:
        subject: The token subject (typically user ID or email).

    Returns:
        Encoded JWT refresh token string.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.refresh_token_expire_days
    )
    payload = {
        "sub": str(subject),
        "exp": expire,
        "type": "refresh",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict | None:
    """Decode and validate a JWT token.

    Args:
        token: The encoded JWT token string.

    Returns:
        Decoded payload dictionary if valid, None if invalid or expired.
    """
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        return payload
    except JWTError:
        return None


def validate_access_token(token: str) -> dict | None:
    """Validate an access token and ensure it is the correct type.

    Returns:
        Decoded payload if valid access token, None otherwise.
    """
    payload = decode_token(token)
    if payload is None:
        return None
    if payload.get("type") != "access":
        return None
    return payload


def validate_refresh_token(token: str) -> dict | None:
    """Validate a refresh token and ensure it is the correct type.

    Returns:
        Decoded payload if valid refresh token, None otherwise.
    """
    payload = decode_token(token)
    if payload is None:
        return None
    if payload.get("type") != "refresh":
        return None
    return payload


# --- Email Verification Token ---


def generate_email_verification_token(email: str) -> str:
    """Generate a JWT token for email verification.

    Args:
        email: The email address to verify.

    Returns:
        Encoded JWT token for email verification (valid for 24 hours).
    """
    expire = datetime.now(timezone.utc) + timedelta(hours=24)
    payload = {
        "sub": email,
        "exp": expire,
        "type": "email_verification",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def validate_email_verification_token(token: str) -> str | None:
    """Validate an email verification token.

    Args:
        token: The encoded email verification token.

    Returns:
        The email address if valid, None otherwise.
    """
    payload = decode_token(token)
    if payload is None:
        return None
    if payload.get("type") != "email_verification":
        return None
    return payload.get("sub")


# --- Password Reset Token ---


def generate_password_reset_token(email: str) -> str:
    """Generate a JWT token for password reset.

    Args:
        email: The email address requesting password reset.

    Returns:
        Encoded JWT token for password reset (valid for 1 hour).
    """
    expire = datetime.now(timezone.utc) + timedelta(hours=1)
    payload = {
        "sub": email,
        "exp": expire,
        "type": "password_reset",
        "jti": secrets.token_hex(16),  # Unique ID to prevent reuse
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def validate_password_reset_token(token: str) -> str | None:
    """Validate a password reset token.

    Args:
        token: The encoded password reset token.

    Returns:
        The email address if valid, None otherwise.
    """
    payload = decode_token(token)
    if payload is None:
        return None
    if payload.get("type") != "password_reset":
        return None
    return payload.get("sub")
