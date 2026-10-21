"""Authentication router with endpoints for registration, login, token refresh,
password reset, and email verification."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.dependencies import get_db
from backend.app.schemas.user_schemas import (
    LoginRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    TokenPair,
    TokenRefreshRequest,
    UserCreate,
    UserResponse,
)
from backend.app.services.auth_service import (
    AccountLockedError,
    AuthService,
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    InvalidTokenError,
    UserNotFoundError,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user account",
)
async def register(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Register a new user account.

    Returns the created user data with a 201 status code.
    Sends a verification email to the provided address.
    """
    service = AuthService(db)
    try:
        return await service.register(user_data)
    except EmailAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": exc.message},
        )


@router.post(
    "/login",
    response_model=TokenPair,
    status_code=status.HTTP_200_OK,
    summary="Authenticate and return JWT tokens",
)
async def login(
    credentials: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenPair:
    """Authenticate a user with email and password.

    Returns access and refresh tokens on success.
    Returns 401 for invalid credentials, 423 if the account is locked.
    """
    service = AuthService(db)
    try:
        return await service.authenticate(credentials)
    except AccountLockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail={
                "code": exc.code,
                "message": exc.message,
                "locked_until": exc.locked_until.isoformat(),
            },
        )
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": exc.code, "message": exc.message},
        )


@router.post(
    "/refresh",
    response_model=TokenPair,
    status_code=status.HTTP_200_OK,
    summary="Refresh access token",
)
async def refresh(
    body: TokenRefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenPair:
    """Exchange a valid refresh token for a new token pair.

    Returns 401 if the refresh token is invalid or expired.
    """
    service = AuthService(db)
    try:
        return await service.refresh_token(body.refresh_token)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": exc.code, "message": exc.message},
        )


@router.post(
    "/password-reset",
    status_code=status.HTTP_200_OK,
    summary="Request password reset email",
)
async def password_reset(
    body: PasswordResetRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Request a password reset link to be sent via email.

    Always returns 200 regardless of whether the email exists
    to prevent email enumeration attacks.
    """
    service = AuthService(db)
    await service.request_password_reset(body.email)
    return {"message": "If the email is registered, a password reset link has been sent."}


@router.post(
    "/password-reset/confirm",
    status_code=status.HTTP_200_OK,
    summary="Confirm password reset with token",
)
async def password_reset_confirm(
    body: PasswordResetConfirm,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Reset the user's password using a valid reset token.

    Returns 401 if the token is invalid or expired.
    """
    service = AuthService(db)
    try:
        await service.confirm_password_reset(body.token, body.new_password)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": exc.code, "message": exc.message},
        )
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": exc.code, "message": exc.message},
        )
    return {"message": "Password has been reset successfully."}


@router.get(
    "/verify-email/{token}",
    status_code=status.HTTP_200_OK,
    summary="Verify email address",
)
async def verify_email(
    token: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Verify a user's email address using the token from the verification link.

    Returns 401 if the token is invalid or expired.
    """
    service = AuthService(db)
    try:
        await service.verify_email(token)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": exc.code, "message": exc.message},
        )
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": exc.code, "message": exc.message},
        )
    return {"message": "Email verified successfully."}
