from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

# Load environment variables from .env.dev file
load_dotenv(dotenv_path=Path('.env.dev').resolve())

class Settings(BaseSettings):
    environment: str = "development"
    database_url: str = Field(default="postgresql://postgres:postgres@localhost:5432/ratinglift", alias="DATABASE_URL")
    mongo_url: str = Field(default="mongodb://localhost:27017/ratinglift", alias="MONGO_URL")
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    jwt_secret: str = Field(default="change-me-jwt-secret", alias="JWT_SECRET")
    admin_jwt_secret: str = Field(default="change-me-admin-jwt-secret", alias="ADMIN_JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    access_token_expiry_minutes: int = Field(default=60, alias="ACCESS_TOKEN_EXPIRY_MINUTES")
    admin_access_token_expiry_minutes: int = Field(default=30, alias="ADMIN_ACCESS_TOKEN_EXPIRY_MINUTES")
    mfa_otp_ttl_seconds: int = Field(default=300, alias="MFA_OTP_TTL_SECONDS")
    login_rate_limit_attempts: int = Field(default=5, alias="LOGIN_RATE_LIMIT_ATTEMPTS")
    login_rate_limit_window_seconds: int = Field(default=60, alias="LOGIN_RATE_LIMIT_WINDOW_SECONDS")
    account_lockout_threshold: int = Field(default=10, alias="ACCOUNT_LOCKOUT_THRESHOLD")
    account_lockout_minutes: int = Field(default=15, alias="ACCOUNT_LOCKOUT_MINUTES")
    refresh_token_expiry_minutes: int = Field(default=60 * 24 * 14, alias="REFRESH_TOKEN_EXPIRY_MINUTES")
    password_reset_ttl_minutes: int = Field(default=30, alias="PASSWORD_RESET_TTL_MINUTES")
    email_verification_ttl_minutes: int = Field(default=60 * 24, alias="EMAIL_VERIFICATION_TTL_MINUTES")
    app_public_url: str = Field(default="http://localhost:8000", alias="APP_PUBLIC_URL")
    smtp_host: str | None = Field(default=None, alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str | None = Field(default=None, alias="SMTP_USER")
    smtp_password: str | None = Field(default=None, alias="SMTP_PASSWORD")
    smtp_from: str | None = Field(default=None, alias="SMTP_FROM")
    smtp_use_tls: bool = Field(default=True, alias="SMTP_USE_TLS")
    twilio_account_sid: str | None = Field(default=None, alias="TWILIO_ACCOUNT_SID")
    twilio_auth_token: str | None = Field(default=None, alias="TWILIO_AUTH_TOKEN")
    twilio_from_number: str | None = Field(default=None, alias="TWILIO_FROM_NUMBER")
    cors_origins: list[str] = ["*"]

    # Media / file uploads
    media_root: str = Field(default="media", alias="MEDIA_ROOT")
    media_url_prefix: str = Field(default="/media", alias="MEDIA_URL_PREFIX")
    connector_logo_max_bytes: int = Field(
        default=2 * 1024 * 1024, alias="CONNECTOR_LOGO_MAX_BYTES"
    )
    connector_logo_max_pixels: int = Field(
        default=2000, alias="CONNECTOR_LOGO_MAX_PIXELS"
    )

    # Storage backend selection. ``local`` writes to MEDIA_ROOT and serves
    # via /media; ``s3`` targets the S3 bucket (implementation pending).
    storage_backend: str = Field(default="local", alias="STORAGE_BACKEND")
    s3_bucket: str | None = Field(default=None, alias="S3_BUCKET")
    s3_region: str | None = Field(default=None, alias="S3_REGION")
    s3_url_base: str | None = Field(default=None, alias="S3_URL_BASE")
    s3_key_prefix: str = Field(default="", alias="S3_KEY_PREFIX")

    model_config = SettingsConfigDict(
        env_file=".env.dev",
        populate_by_name=True,
        extra="ignore",
    )

settings = Settings()
