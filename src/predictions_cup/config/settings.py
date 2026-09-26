"""Runtime configuration with fail-closed secret handling."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import AnyHttpUrl, AnyUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
PolymarketUniverse = Literal["us_elections_2026"]


class AppSettings(BaseSettings):
    """Canonical application settings loaded from runtime configuration."""

    model_config = SettingsConfigDict(
        env_prefix="PREDICTIONS_CUP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        validate_default=True,
    )

    environment: str = "development"
    log_level: LogLevel = "INFO"
    sig_api_base_url: AnyHttpUrl = AnyHttpUrl("https://www.thesuper.market/api/v1")
    sig_read_credential: SecretStr | None = None
    sig_trade_credential: SecretStr | None = None
    tournament_id: str | None = None
    tournament_slug: str | None = None
    trading_enabled: bool = False

    sig_realtime_storage_path: Path = Path("data/sig_realtime.sqlite3")
    sig_realtime_book_depth: int = Field(default=20, ge=1, le=200)
    sig_realtime_tracked_exchange_ids: str = ""
    sig_rest_governor_rate_per_second: float = Field(default=2.0, gt=0.0, le=100.0)
    sig_rest_shared_cooldown_max_seconds: float = Field(default=8.0, ge=0.5, le=120.0)
    sig_realtime_open_book_refresh_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    sig_realtime_bulk_price_refresh_seconds: float = Field(default=10.0, ge=1.0, le=300.0)
    sig_realtime_token_refresh_margin_seconds: float = Field(default=300.0, ge=30, le=1800)
    sig_realtime_retention_days: int = Field(default=14, ge=1, le=90)

    polymarket_capture_enabled: bool = False
    polymarket_gamma_base_url: AnyHttpUrl = AnyHttpUrl("https://gamma-api.polymarket.com")
    polymarket_clob_base_url: AnyHttpUrl = AnyHttpUrl("https://clob.polymarket.com")
    polymarket_ws_url: AnyUrl = AnyUrl(
        "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    )
    polymarket_snapshot_interval_seconds: float = Field(default=1.0, gt=0)
    polymarket_book_depth: int = Field(default=20, ge=1, le=200)
    polymarket_depth_snapshot_interval_seconds: float = Field(default=60.0, ge=1)
    polymarket_gamma_page_limit: int = Field(default=100, ge=1, le=500)
    polymarket_gamma_refresh_seconds: float = Field(default=300.0, ge=30)
    polymarket_storage_path: Path = Path("data/polymarket_capture.sqlite3")
    polymarket_universe: PolymarketUniverse = "us_elections_2026"
    polymarket_include_ids: str = ""
    polymarket_exclude_ids: str = ""

    @field_validator("environment", "tournament_id", "tournament_slug")
    @classmethod
    def reject_blank_optional_strings(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("configuration string must not be blank")
        return value

    @field_validator("polymarket_storage_path", "sig_realtime_storage_path")
    @classmethod
    def reject_blank_storage_path(cls, value: Path) -> Path:
        if not str(value).strip():
            raise ValueError("polymarket_storage_path must not be blank")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalise_log_level(cls, value: object) -> object:
        if isinstance(value, str):
            return value.upper()
        return value

    @field_validator("sig_read_credential", "sig_trade_credential")
    @classmethod
    def reject_blank_credentials(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("credential must not be blank")
        return value

    @model_validator(mode="after")
    def fail_closed_trading_configuration(self) -> Self:
        if self.trading_enabled and self.sig_trade_credential is None:
            raise ValueError("trading_enabled requires an explicitly supplied trade credential")
        return self

    def diagnostic_fields(self) -> dict[str, str | bool | int | float | None]:
        """Return deliberately non-secret diagnostics suitable for logs."""
        return {
            "environment": self.environment,
            "log_level": self.log_level,
            "sig_api_base_url": str(self.sig_api_base_url),
            "tournament_id": self.tournament_id,
            "tournament_slug": self.tournament_slug,
            "trading_enabled": self.trading_enabled,
            "sig_read_credential_configured": self.sig_read_credential is not None,
            "sig_trade_credential_configured": self.sig_trade_credential is not None,
            "sig_realtime_storage_path": str(self.sig_realtime_storage_path),
            "sig_realtime_book_depth": self.sig_realtime_book_depth,
            "sig_realtime_tracked_exchange_count": len(
                {
                    value.strip()
                    for value in self.sig_realtime_tracked_exchange_ids.split(",")
                    if value.strip()
                }
            ),
            "sig_rest_governor_rate_per_second": self.sig_rest_governor_rate_per_second,
            "sig_rest_shared_cooldown_max_seconds": (
                self.sig_rest_shared_cooldown_max_seconds
            ),
            "sig_realtime_open_book_refresh_seconds": (
                self.sig_realtime_open_book_refresh_seconds
            ),
            "sig_realtime_bulk_price_refresh_seconds": (
                self.sig_realtime_bulk_price_refresh_seconds
            ),
            "sig_realtime_token_refresh_margin_seconds": (
                self.sig_realtime_token_refresh_margin_seconds
            ),
            "sig_realtime_retention_days": self.sig_realtime_retention_days,
            "polymarket_capture_enabled": self.polymarket_capture_enabled,
            "polymarket_universe": self.polymarket_universe,
            "polymarket_snapshot_interval_seconds": self.polymarket_snapshot_interval_seconds,
            "polymarket_book_depth": self.polymarket_book_depth,
            "polymarket_depth_snapshot_interval_seconds": (
                self.polymarket_depth_snapshot_interval_seconds
            ),
            "polymarket_storage_path": str(self.polymarket_storage_path),
        }


class _RuntimeAppSettings(AppSettings):
    """System-service settings source: process environment only, never repo .env."""

    model_config = SettingsConfigDict(
        env_prefix="PREDICTIONS_CUP_",
        env_file=None,
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        validate_default=True,
    )


def load_settings(*, use_dotenv: bool = True) -> AppSettings:
    """Load settings on demand; runtime services can explicitly disable local dotenv."""
    if not use_dotenv:
        return _RuntimeAppSettings()
    return AppSettings()
