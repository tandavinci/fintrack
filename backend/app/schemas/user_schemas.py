"""Pydantic schemas for user authentication and account management."""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    """Schema for user registration request."""

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str = Field(..., min_length=1, max_length=255)


class UserResponse(BaseModel):
    """Schema for user data returned in API responses."""

    id: int
    email: str
    full_name: str
    is_verified: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    """Schema for user login request."""

    email: EmailStr
    password: str = Field(..., min_length=1)


class TokenPair(BaseModel):
    """Schema for JWT access and refresh token pair."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class PasswordResetRequest(BaseModel):
    """Schema for initiating a password reset."""

    email: EmailStr


class PasswordResetConfirm(BaseModel):
    """Schema for confirming a password reset with a token."""

    token: str
    new_password: str = Field(..., min_length=8, max_length=128)


class TokenRefreshRequest(BaseModel):
    """Schema for refreshing an access token."""

    refresh_token: str
