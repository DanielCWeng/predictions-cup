"""Runtime configuration with fail-closed secret handling."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import AnyHttpUrl, AnyUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
ExecutionModeSetting = Literal["SHADOW", "LIVE"]
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
    execution_mode: ExecutionModeSetting = "SHADOW"
    global_kill_switch: bool = True
    execution_journal_path: Path = Path("data/execution_journal.sqlite3")
    risk_max_order_size: int | None = Field(default=None, gt=0, le=2_147_483_647)
    risk_max_gross_exposure: float | None = Field(default=None, gt=0.0)
    risk_max_per_market_exposure: float | None = Field(default=None, gt=0.0)
    risk_max_open_order_exposure: float | None = Field(default=None, gt=0.0)
    risk_max_concurrent_open_orders: int | None = Field(default=None, gt=0)
    risk_max_state_age_ms: int = Field(default=1_000, gt=0)

    # MAKE-001 is explicitly opt-in. LIVE is still separately guarded by the
    # BUILD-009 execution_mode/trading_enabled/interlock controls.
    maker_enabled: bool = False
    maker_mapping_path: Path = Path("data/mappings/sig_polymarket_2026.json")
    maker_max_abs_inventory: float = Field(default=10.0, gt=0.0)
    maker_base_size: int = Field(default=2, gt=0, le=2_147_483_647)
    maker_minimum_size: int = Field(default=1, gt=0, le=2_147_483_647)
    maker_base_half_spread_ticks: float = Field(default=1.0, ge=0.5)
    maker_bbo_max_age_ms: int = Field(default=1_000, gt=0)
    maker_fv_max_age_ms: int = Field(default=1_000, gt=0)
    maker_account_max_age_ms: int = Field(default=2_000, gt=0)
    maker_inventory_max_age_ms: int = Field(default=2_000, gt=0)
    maker_signal_max_age_ms: int = Field(default=1_000, gt=0)
    maker_require_trusted_depth: bool = False
    maker_depth_max_age_ms: int = Field(default=1_000, gt=0)
    maker_min_replace_ticks: int = Field(default=1, gt=0)
    maker_min_replace_size: int = Field(default=1, gt=0)
    maker_min_requote_interval_ms: int = Field(default=0, ge=0)

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
    polymarket_storage_path: Path = Path("data/polymarket_operational.sqlite3")
    polymarket_research_path: Path = Path("data/polymarket_research")
    polymarket_parquet_shard_seconds: int = Field(default=60, ge=30, le=300)
    polymarket_parquet_max_rows_per_shard: int = Field(default=100_000, ge=1_000, le=1_000_000)
    polymarket_universe: PolymarketUniverse = "us_elections_2026"
    polymarket_include_ids: str = ""
    polymarket_exclude_ids: str = ""
    polymarket_supervised_ids: str = ""

    @field_validator("environment", "tournament_id", "tournament_slug")
    @classmethod
    def reject_blank_optional_strings(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("configuration string must not be blank")
        return value

    @field_validator(
        "polymarket_storage_path",
        "polymarket_research_path",
        "sig_realtime_storage_path",
        "execution_journal_path",
        "maker_mapping_path",
    )
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
    def validate_maker_configuration(self) -> Self:
        if self.maker_minimum_size > self.maker_base_size:
            raise ValueError("maker_minimum_size cannot exceed maker_base_size")
        return self

    @model_validator(mode="after")
    def fail_closed_trading_configuration(self) -> Self:
        if self.trading_enabled and self.sig_trade_credential is None:
            raise ValueError("trading_enabled requires an explicitly supplied trade credential")
        if self.execution_mode == "LIVE":
            if not self.trading_enabled:
                raise ValueError("LIVE execution requires trading_enabled=true")
            if self.sig_trade_credential is None:
                raise ValueError("LIVE execution requires an explicit trade credential")
            if self.tournament_id is None or self.tournament_slug is None:
                raise ValueError(
                    "LIVE execution requires explicit tournament_id and tournament_slug"
                )
            limits = (
                self.risk_max_order_size,
                self.risk_max_gross_exposure,
                self.risk_max_per_market_exposure,
                self.risk_max_open_order_exposure,
                self.risk_max_concurrent_open_orders,
            )
            if any(value is None for value in limits):
                raise ValueError("LIVE execution requires every central risk cap")
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
            "execution_mode": self.execution_mode,
            "global_kill_switch": self.global_kill_switch,
            "execution_journal_path": str(self.execution_journal_path),
            "risk_max_order_size": self.risk_max_order_size,
            "risk_max_gross_exposure": self.risk_max_gross_exposure,
            "risk_max_per_market_exposure": self.risk_max_per_market_exposure,
            "risk_max_open_order_exposure": self.risk_max_open_order_exposure,
            "risk_max_concurrent_open_orders": self.risk_max_concurrent_open_orders,
            "risk_max_state_age_ms": self.risk_max_state_age_ms,
            "maker_enabled": self.maker_enabled,
            "maker_mapping_path": str(self.maker_mapping_path),
            "maker_max_abs_inventory": self.maker_max_abs_inventory,
            "maker_base_size": self.maker_base_size,
            "maker_minimum_size": self.maker_minimum_size,
            "maker_base_half_spread_ticks": self.maker_base_half_spread_ticks,
            "maker_bbo_max_age_ms": self.maker_bbo_max_age_ms,
            "maker_fv_max_age_ms": self.maker_fv_max_age_ms,
            "maker_account_max_age_ms": self.maker_account_max_age_ms,
            "maker_inventory_max_age_ms": self.maker_inventory_max_age_ms,
            "maker_signal_max_age_ms": self.maker_signal_max_age_ms,
            "maker_require_trusted_depth": self.maker_require_trusted_depth,
            "maker_depth_max_age_ms": self.maker_depth_max_age_ms,
            "maker_min_replace_ticks": self.maker_min_replace_ticks,
            "maker_min_replace_size": self.maker_min_replace_size,
            "maker_min_requote_interval_ms": self.maker_min_requote_interval_ms,
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
            "polymarket_research_path": str(self.polymarket_research_path),
            "polymarket_supervised_id_count": len(
                {
                    value.strip()
                    for value in self.polymarket_supervised_ids.split(",")
                    if value.strip()
                }
            ),
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
