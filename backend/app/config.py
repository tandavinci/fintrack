from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/bse_tracker",
        description="Async PostgreSQL connection URL",
    )
    # Optional explicit sync URL (psycopg2). When unset, the shared sync engine
    # in database.py derives it from database_url (asyncpg -> psycopg2 swap).
    database_url_sync: str | None = Field(
        default=None,
        description="Sync PostgreSQL connection URL (psycopg2). Derived from database_url when None.",
    )

    # Redis
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL for caching and Celery broker",
    )

    # JWT Authentication
    jwt_secret: str = Field(
        default="change-me-in-production",
        description="Secret key for JWT token signing",
    )
    jwt_algorithm: str = Field(default="HS256")
    access_token_expire_minutes: int = Field(default=30)
    refresh_token_expire_days: int = Field(default=7)

    # BSE API Configuration
    bse_api_base_url: str = Field(
        default="https://api.bseindia.com",
        description="BSE India API base URL",
    )
    bse_price_update_interval_seconds: int = Field(
        default=60,
        description="Interval for fetching stock price updates during market hours",
    )

    # Email / SMTP Settings
    smtp_host: str = Field(default="localhost")
    smtp_port: int = Field(default=587)
    smtp_username: str = Field(default="")
    smtp_password: str = Field(default="")
    smtp_from_email: str = Field(default="noreply@bsetracker.com")
    smtp_use_tls: bool = Field(default=True)

    # News ingest (FEAT B — placeholders registered here so the shell loads;
    # the ingest service/repository arrive in FEAT-003).
    news_rss_feeds: list[str] = Field(
        default=[],
        description="Configurable list of market-news RSS feed URLs (FEAT B).",
    )
    news_bse_announcements_enabled: bool = Field(
        default=False,
        description="Feature flag for best-effort BSE announcements ingest (FEAT B).",
    )

    # Application
    app_name: str = Field(default="BSE Stock Investment Tracker")
    debug: bool = Field(default=False)
    allowed_origins: list[str] = Field(
        default=["http://localhost:5173", "http://localhost:3000"],
        description="CORS allowed origins",
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
