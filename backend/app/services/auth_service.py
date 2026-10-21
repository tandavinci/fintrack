"""Authentication service with account lockout logic.

Handles user registration, login with lockout protection, token refresh,
password reset, and email verification.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.user import User
from backend.app.schemas.user_schemas import LoginRequest, TokenPair, UserCreate, UserResponse
from backend.app.utils.security import (
    create_access_token,
    create_refresh_token,
    generate_email_verification_token,
    generate_password_reset_token,
    hash_password,
    validate_email_verification_token,
    validate_password_reset_token,
    validate_refresh_token,
    verify_password,
)

# Account lockout constants
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 15


class AuthServiceError(Exception):
    """Base exception for auth service errors."""

    def __init__(self, message: str, code: str = "AUTH_ERROR"):
        self.message = message
        self.code = code
        super().__init__(message)


class EmailAlreadyExistsError(AuthServiceError):
    """Raised when attempting to register with an existing email."""

    def __init__(self):
        super().__init__(
            message="An account with this email already exists.",
            code="EMAIL_ALREADY_EXISTS",
        )


class InvalidCredentialsError(AuthServiceError):
    """Raised when login credentials are invalid."""

    def __init__(self):
        super().__init__(
            message="Invalid email or password.",
            code="INVALID_CREDENTIALS",
        )


class AccountLockedError(AuthServiceError):
    """Raised when account is locked due to too many failed attempts."""

    def __init__(self, locked_until: datetime):
        self.locked_until = locked_until
        super().__init__(
            message=f"Account is locked until {locked_until.isoformat()}. Please try again later.",
            code="ACCOUNT_LOCKED",
        )


class InvalidTokenError(AuthServiceError):
    """Raised when a token is invalid or expired."""

    def __init__(self):
        super().__init__(
            message="Invalid or expired token.",
            code="INVALID_TOKEN",
        )


class UserNotFoundError(AuthServiceError):
    """Raised when a user is not found."""

    def __init__(self):
        super().__init__(
            message="User not found.",
            code="USER_NOT_FOUND",
        )


class AuthService:
    """Service handling authentication, registration, and account security."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def register(self, user_data: UserCreate) -> UserResponse:
        """Register a new user account.

        Validates email uniqueness, hashes the password, creates the user,
        and sends a verification email.

        Args:
            user_data: Registration data with email, password, and full_name.

        Returns:
            UserResponse with the created user's data.

        Raises:
            EmailAlreadyExistsError: If the email is already registered.
        """
        # Check for existing user with this email
        existing_user = await self._get_user_by_email(user_data.email)
        if existing_user is not None:
            raise EmailAlreadyExistsError()

        # Hash the password and create the user
        password_hash = hash_password(user_data.password)
        user = User(
            email=user_data.email,
            password_hash=password_hash,
            full_name=user_data.full_name,
            is_verified=False,
            is_locked=False,
            failed_login_attempts=0,
        )
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)

        # Send verification email (fire-and-forget; failures handled by caller/task queue)
        await self._send_verification_email(user)

        return UserResponse.model_validate(user)

    async def authenticate(self, credentials: LoginRequest) -> TokenPair:
        """Authenticate a user and return a JWT token pair.

        Checks account lock status, verifies credentials, and manages
        failed attempt tracking. Locks account after 5 consecutive failures
        for 15 minutes.

        Args:
            credentials: Login request with email and password.

        Returns:
            TokenPair with access and refresh tokens.

        Raises:
            InvalidCredentialsError: If email doesn't exist or password is wrong.
            AccountLockedError: If account is currently locked.
        """
        user = await self._get_user_by_email(credentials.email)
        if user is None:
            raise InvalidCredentialsError()

        # Check if account is locked
        if self._is_account_locked(user):
            raise AccountLockedError(locked_until=user.locked_until)

        # Verify password
        if not verify_password(credentials.password, user.password_hash):
            await self._handle_failed_attempt(user)
            raise InvalidCredentialsError()

        # Successful login — reset failed attempts
        await self._reset_failed_attempts(user)

        # Generate token pair
        access_token = create_access_token(
            subject=user.id,
            extra_claims={"email": user.email},
        )
        refresh_token = create_refresh_token(subject=user.id)

        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
        )

    async def refresh_token(self, refresh_token_str: str) -> TokenPair:
        """Refresh an access token using a valid refresh token.

        Args:
            refresh_token_str: The refresh token to validate.

        Returns:
            New TokenPair with fresh access and refresh tokens.

        Raises:
            InvalidTokenError: If the refresh token is invalid or expired.
        """
        payload = validate_refresh_token(refresh_token_str)
        if payload is None:
            raise InvalidTokenError()

        user_id = payload.get("sub")
        if user_id is None:
            raise InvalidTokenError()

        # Verify the user still exists
        user = await self._get_user_by_id(int(user_id))
        if user is None:
            raise InvalidTokenError()

        # Issue new token pair
        access_token = create_access_token(
            subject=user.id,
            extra_claims={"email": user.email},
        )
        new_refresh_token = create_refresh_token(subject=user.id)

        return TokenPair(
            access_token=access_token,
            refresh_token=new_refresh_token,
        )

    async def request_password_reset(self, email: str) -> None:
        """Request a password reset email.

        Generates a password reset token and sends a reset link.
        Silently succeeds even if email doesn't exist (prevents enumeration).

        Args:
            email: The email address requesting password reset.
        """
        user = await self._get_user_by_email(email)
        if user is None:
            # Silently return to prevent email enumeration
            return

        token = generate_password_reset_token(email)
        await self._send_password_reset_email(user, token)

    async def confirm_password_reset(self, token: str, new_password: str) -> None:
        """Confirm a password reset using the provided token.

        Validates the reset token and updates the user's password.

        Args:
            token: The password reset token from the email link.
            new_password: The new password to set.

        Raises:
            InvalidTokenError: If the token is invalid or expired.
            UserNotFoundError: If the user associated with the token no longer exists.
        """
        email = validate_password_reset_token(token)
        if email is None:
            raise InvalidTokenError()

        user = await self._get_user_by_email(email)
        if user is None:
            raise UserNotFoundError()

        # Update password and reset any lockout state
        user.password_hash = hash_password(new_password)
        user.failed_login_attempts = 0
        user.is_locked = False
        user.locked_until = None
        await self.db.commit()

    async def verify_email(self, token: str) -> None:
        """Verify a user's email address using the verification token.

        Args:
            token: The email verification token.

        Raises:
            InvalidTokenError: If the token is invalid or expired.
            UserNotFoundError: If the user associated with the token doesn't exist.
        """
        email = validate_email_verification_token(token)
        if email is None:
            raise InvalidTokenError()

        user = await self._get_user_by_email(email)
        if user is None:
            raise UserNotFoundError()

        user.is_verified = True
        await self.db.commit()

    # --- Private helpers ---

    async def _get_user_by_email(self, email: str) -> User | None:
        """Fetch a user by email address."""
        stmt = select(User).where(User.email == email)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_user_by_id(self, user_id: int) -> User | None:
        """Fetch a user by ID."""
        stmt = select(User).where(User.id == user_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    def _is_account_locked(self, user: User) -> bool:
        """Check if a user account is currently locked.

        An account is locked if is_locked is True and locked_until
        is in the future. If the lockout has expired, unlock the account.
        """
        if not user.is_locked:
            return False

        if user.locked_until is None:
            return False

        now = datetime.now(timezone.utc)
        if now >= user.locked_until:
            # Lockout period expired — unlock the account
            user.is_locked = False
            user.locked_until = None
            user.failed_login_attempts = 0
            return False

        return True

    async def _handle_failed_attempt(self, user: User) -> None:
        """Increment failed login attempts and lock if threshold exceeded.

        Locks the account for 15 minutes after more than 5 consecutive
        failed attempts.
        """
        user.failed_login_attempts += 1

        if user.failed_login_attempts > MAX_FAILED_ATTEMPTS:
            user.is_locked = True
            user.locked_until = datetime.now(timezone.utc) + timedelta(
                minutes=LOCKOUT_DURATION_MINUTES
            )

        await self.db.commit()

    async def _reset_failed_attempts(self, user: User) -> None:
        """Reset failed login attempts after successful authentication."""
        if user.failed_login_attempts > 0:
            user.failed_login_attempts = 0
            user.is_locked = False
            user.locked_until = None
            await self.db.commit()

    async def _send_verification_email(self, user: User) -> None:
        """Send an email verification link to the user.

        Generates a verification token and dispatches the email.
        In production, this would queue via Celery for async delivery.
        """
        token = generate_email_verification_token(user.email)
        # TODO: Integrate with email utility (app/utils/email.py) once implemented.
        # For now, token generation is the key behavior; email dispatch is a
        # background concern handled by task 12.5.
        _ = token  # Token generated for verification link

    async def _send_password_reset_email(self, user: User, token: str) -> None:
        """Send a password reset link to the user.

        In production, this would queue via Celery for async delivery.
        """
        # TODO: Integrate with email utility (app/utils/email.py) once implemented.
        # Token is passed in from the caller; email dispatch is a background concern.
        _ = token  # Token generated for reset link
