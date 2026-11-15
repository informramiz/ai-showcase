"""Typed settings, loaded from the environment (and an optional .env file)."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderChoice(StrEnum):
    AUTO = "auto"  # Anthropic when ANTHROPIC_API_KEY is set, otherwise the fake
    FAKE = "fake"
    ANTHROPIC = "anthropic"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SOURCEBOOK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "SOURCEBOOK_ANTHROPIC_API_KEY"),
    )
    anthropic_base_url: AnyHttpUrl | None = Field(
        default=None,
        validation_alias=AliasChoices("ANTHROPIC_BASE_URL", "SOURCEBOOK_ANTHROPIC_BASE_URL"),
        description="Anthropic-compatible gateway, e.g. https://openrouter.ai/api",
    )
    provider: ProviderChoice = ProviderChoice.AUTO
    model: str = "claude-opus-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    max_output_tokens: int = Field(default=16_000, ge=1_024, le=128_000)
    max_agent_turns: int = Field(default=4, ge=1, le=10)
    search_top_k: int = Field(default=4, ge=1, le=8)  # tool schema allows at most 8

    database_path: Path = Path("data/sourcebook.db")
    seed_samples: bool = True
    max_upload_bytes: int = Field(default=512_000, ge=1_000)

    host: str = "127.0.0.1"
    port: int = 8000

    @property
    def use_anthropic(self) -> bool:
        if self.provider is ProviderChoice.FAKE:
            return False
        if self.provider is ProviderChoice.ANTHROPIC:
            return True
        return self.anthropic_api_key is not None and bool(
            self.anthropic_api_key.get_secret_value().strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
