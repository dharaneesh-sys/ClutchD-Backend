from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "MechOnCall API"
    debug: bool = False
    api_prefix: str = "/api"

    database_url: str = "postgresql+asyncpg://mechoncall:mechoncall@localhost:5432/mechoncall"
    sync_database_url: str = "postgresql://mechoncall:mechoncall@localhost:5432/mechoncall"

    redis_url: str = "redis://localhost:6379/0"

    jwt_secret_key: str = "change-me-in-production-use-openssl-rand-hex-32"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7

    default_map_lat: float = 11.0168
    default_map_lon: float = 76.9558

    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None

    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = None

    upload_dir: str = "uploads"
    max_upload_mb: int = 10

    rate_limit_auth: str = "20/minute"

    # When true, new mechanics/garages are verified immediately (local demos only).
    dev_auto_verify_providers: bool = False

    celery_broker_url: str | None = None
    celery_result_backend: str | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def celery_broker(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def celery_backend(self) -> str:
        return self.celery_result_backend or self.redis_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
