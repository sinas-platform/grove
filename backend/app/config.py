from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    grove_database_url: str = Field(
        default="postgresql+asyncpg://grove:grove@localhost:5432/grove",
        validation_alias="GROVE_DATABASE_URL",
    )

    # Server
    grove_port: int = Field(default=8080, validation_alias="GROVE_PORT")
    grove_log_level: str = Field(default="INFO", validation_alias="GROVE_LOG_LEVEL")
    grove_cors_origins: str = Field(default="", validation_alias="GROVE_CORS_ORIGINS")

    # Auth
    grove_auth_mode: Literal["sinas", "simplified"] = Field(
        default="sinas", validation_alias="GROVE_AUTH_MODE"
    )

    # Sinas integration
    sinas_url: str = Field(default="http://localhost:8000", validation_alias="SINAS_URL")

    # Required when GROVE_AUTH_MODE=simplified — the Sinas API key Grove uses
    # for ALL Sinas callbacks (skills, files, etc) and to derive the single
    # admin identity via /auth/me. Unused in `sinas` mode (per-user tokens
    # carry their own auth).
    sinas_api_key: str = Field(default="", validation_alias="SINAS_API_KEY")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.grove_cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
