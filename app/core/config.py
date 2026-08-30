"""Application configuration module.

This module defines the ApiSettings class which loads configuration
from environment variables and .env files. It includes validation
for critical secrets required in production.
"""

import logging
import sys
from functools import lru_cache
from typing import Literal
from urllib.parse import quote

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("storeflow.config")


class ApiSettings(BaseSettings):
    """Application settings loaded from environment variables.

    All settings can be overridden via environment variables or .env file.
    Critical secrets (JWT_ACCESS_SECRET, JWT_REFRESH_SECRET, SECRET_KEY)
    are validated at startup in production mode.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "StoreFlow"
    app_version: str = "0.1.0"
    env: Literal["development", "staging", "production"] = "development"
    debug: bool = False

    secret_key: str = Field(default="", alias="SECRET_KEY")
    allowed_origins: list[str] = Field(
        default=["http://localhost:8080", "http://localhost:8000", "http://localhost:8001"],
        alias="ALLOWED_ORIGINS",
    )

    db_user: str = Field(default="storeflow_app", alias="DATABASE_USER")
    db_password: str = Field(default="", alias="DATABASE_PASSWORD")
    db_name: str = Field(default="storeflow", alias="DATABASE_NAME")
    database_url: str = ""
    database_url_sync: str = ""
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_echo: bool = False

    def model_post_init(self, __context) -> None:
        pw = quote(self.db_password) if self.db_password else ""
        user = self.db_user
        db = self.db_name
        ssl = "?sslmode=require" if self.is_production else ""
        if not self.database_url:
            self.database_url = f"postgresql+asyncpg://{user}:{pw}@localhost:5432/{db}{ssl}"
        if not self.database_url_sync:
            self.database_url_sync = f"postgresql+psycopg2://{user}:{pw}@localhost:5432/{db}{ssl}"

    redis_url: str = "redis://localhost:6379/0"
    redis_cache_db: int = 1
    redis_session_db: int = 2

    jwt_algorithm: str = "HS256"
    jwt_access_secret: str = Field(default="", alias="JWT_ACCESS_SECRET")
    jwt_refresh_secret: str = Field(default="", alias="JWT_REFRESH_SECRET")
    access_token_expire_minutes: int = 600
    refresh_token_expire_days: int = 7

    totp_issuer: str = "StoreFlow"
    password_bcrypt_rounds: int = 12

    celery_broker_url: str = Field(default="amqp://storeflow:storeflow@localhost:5672//")
    celery_result_backend: str = "redis://localhost:6379/3"

    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = "noreply@storeflow.ng"
    smtp_password: str = ""
    email_from_name: str = "StoreFlow"

    resend_api_key: str = Field(default="", alias="RESEND_API_KEY")
    resend_from_email: str = Field(default="noreply@storeflow.ng", alias="RESEND_FROM_EMAIL")

    termii_api_key: str = Field(default="", alias="TERMII_API_KEY")
    termii_from: str = Field(default="StoreFlow", alias="TERMII_FROM")
    termii_base_url: str = Field(
        default="https://api.termii.com/api", alias="TERMII_BASE_URL"
    )

    flutterwave_secret_key: str = Field(default="", alias="FLW_SECRET_KEY")
    flutterwave_public_key: str = Field(default="", alias="FLUTTERWAVE_PUBLIC_KEY")
    flutterwave_secret_hash: str = Field(default="", alias="FLW_SECRET_HASH")
    flutterwave_base_url: str = Field(
        default="https://api.flutterwave.com/v3", alias="FLUTTERWAVE_BASE_URL"
    )

    api_base_url: str = Field(
        default="http://localhost:8000", alias="API_BASE_URL"
    )

    supervisor_pin_expire_days: int = 7

    @property
    def redis_cache_url(self) -> str:
        return self.redis_url.rsplit("/", 1)[0] + f"/{self.redis_cache_db}"

    @property
    def redis_session_url(self) -> str:
        return self.redis_url.rsplit("/", 1)[0] + f"/{self.redis_session_db}"

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    def validate_production_secrets(self) -> None:
        """Validate that critical secrets are set in production.

        Raises:
            SystemExit: If required secrets are missing in production.
        """
        if not self.is_production:
            return

        missing = []
        if not self.secret_key:
            missing.append("SECRET_KEY")
        if not self.jwt_access_secret:
            missing.append("JWT_ACCESS_SECRET")
        if not self.jwt_refresh_secret:
            missing.append("JWT_REFRESH_SECRET")
        if not self.flutterwave_secret_key:
            missing.append("FLW_SECRET_KEY")
        if not self.database_url or "localhost" in self.database_url:
            missing.append("DATABASE_URL (must not be localhost)")

        if missing:
            logger.critical(
                "Missing required environment variables for production: %s",
                ", ".join(missing),
            )
            sys.exit(1)


@lru_cache
def get_settings() -> ApiSettings:
    return ApiSettings()


settings = get_settings()
