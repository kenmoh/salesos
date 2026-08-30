from functools import lru_cache
from typing import Literal
from urllib.parse import quote

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CommonSettings(BaseSettings):
    """Runtime settings shared by API, workers, and internal service packages.

    All database_url_* settings point to the same PostgreSQL database.
    The schema is set via SET search_path in the ServiceDatabase context manager.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "SalesOS"
    app_version: str = "0.1.0"
    env: Literal["development", "staging", "production"] = "development"
    debug: bool = False

    database_url: str = ""

    db_user: str = Field(default="storeflow_app", alias="DATABASE_USER")
    db_password: str = Field(default="", alias="DATABASE_PASSWORD")
    db_name: str = Field(default="storeflow", alias="DATABASE_NAME")

    rabbitmq_url: str = Field(default="", alias="RABBIT_MQ_URL")
    celery_broker_url: str = Field(default="", alias="RABBIT_MQ_URL")
    celery_result_backend: str = "redis://localhost:6379/3"

    redis_url: str = "redis://localhost:6379/0"

    ai_groq_api_key: str = ""
    ai_model: str = "llama-3.3-70b-versatile"

    cloudinary_cloud_name: str = Field(default="", alias="CLOUDINARY_CLOUD_NAME")
    cloudinary_api_key: str = Field(default="", alias="CLOUDINARY_API_KEY")
    cloudinary_api_secret: str = Field(default="", alias="CLOUDINARY_SECRET")

    def model_post_init(self, __context) -> None:
        if not self.database_url:
            pw = quote(self.db_password) if self.db_password else ""
            user = self.db_user
            db = self.db_name
            ssl = "?sslmode=require" if self.is_production else ""
            self.database_url = f"postgresql+asyncpg://{user}:{pw}@localhost:5432/{db}{ssl}"

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_common_settings() -> CommonSettings:
    return CommonSettings()
