"""Runtime configuration with fail-closed secret handling."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import AnyHttpUrl, AnyUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
ExecutionModeSetting = Literal["SHADOW", "LIVE"]
RiskProfileMode = Literal["STANDARD", "EXPLORATORY"]
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
    risk_max_per_strategy_exposure: float | None = Field(default=None, gt=0.0)
    risk_max_event_group_exposure: float | None = Field(default=None, gt=0.0)
    risk_max_tournament_exposure: float | None = Field(default=None, gt=0.0)
    risk_session_loss_limit: float | None = Field(default=None, gt=0.0)
    risk_drawdown_limit: float | None = Field(default=None, gt=0.0)
    risk_max_state_age_ms: int = Field(default=1_000, gt=0)
    risk_max_account_age_ms: int = Field(default=2_000, gt=0)
    risk_max_mark_age_ms: int = Field(default=12_000, gt=0)
    risk_capital_control_enabled: bool = False
    risk_state_path: Path = Path("data/risk_002.sqlite3")
    risk_exposure_groups_path: Path | None = None
    risk_profile_name: str = "competition"
    risk_profile_version: str = "risk-002-v1"
    risk_profile_mode: RiskProfileMode = "STANDARD"
    risk_exploratory_max_order_size: int | None = Field(default=None, gt=0, le=2_147_483_647)
    risk_exploratory_max_gross_exposure: float | None = Field(default=None, gt=0.0)
    risk_exploratory_max_per_market_exposure: float | None = Field(default=None, gt=0.0)
    risk_exploratory_max_open_order_exposure: float | None = Field(default=None, gt=0.0)
    risk_exploratory_max_concurrent_open_orders: int | None = Field(default=None, gt=0)
    risk_exploratory_max_per_strategy_exposure: float | None = Field(default=None, gt=0.0)
    risk_exploratory_max_event_group_exposure: float | None = Field(default=None, gt=0.0)
    risk_exploratory_max_tournament_exposure: float | None = Field(default=None, gt=0.0)

    # MAKE-001 is disabled by default. These are calculation/runtime parameters,
    # not substitutes for BUILD-009 central risk limits.
    maker_enabled: bool = False
    maker_mapping_path: Path = Path("data/mappings/sig_polymarket_2026.json")
    maker_max_abs_inventory: float = Field(default=10.0, gt=0.0)
    maker_base_size: int = Field(default=2, gt=0, le=2_147_483_647)
    maker_minimum_size: int = Field(default=1, gt=0, le=2_147_483_647)
    maker_base_half_spread_ticks: float = Field(default=1.0, ge=0.0)
    maker_inventory_risk_aversion: float = Field(default=0.02, ge=0.0)
    maker_uncertainty_multiplier: float = Field(default=1.0, ge=0.0)
    maker_volatility_multiplier: float = Field(default=0.5, ge=0.0)
    maker_toxicity_half_spread_ticks: float = Field(default=4.0, ge=0.0)
    maker_max_bbo_age_ms: int = Field(default=12_000, gt=0)
    maker_max_fv_age_ms: int = Field(default=1_000, gt=0)
    maker_account_refresh_interval_seconds: float = Field(default=5.0, gt=0.0)
    maker_max_account_age_ms: int = Field(default=15_000, gt=0)
    maker_max_inventory_age_ms: int = Field(default=2_000, gt=0)
    maker_max_signal_age_ms: int = Field(default=1_000, gt=0)
    maker_require_trusted_depth: bool = False
    maker_max_depth_age_ms: int = Field(default=35_000, gt=0)
    maker_min_replace_ticks: int = Field(default=1, gt=0)
    maker_min_replace_size: int = Field(default=1, gt=0)
    maker_min_requote_interval_ms: int = Field(default=0, ge=0)
    maker_min_fair_value: float = Field(default=0.0, ge=0.0, le=1.0)
    maker_max_fair_value: float = Field(default=1.0, ge=0.0, le=1.0)

    # SIG account shadow projection is deliberately opt-in. PROXY only follows a
    # recent clean REST snapshot and identified local execution/realtime events.
    account_proxy_enabled: bool = False
    account_proxy_max_age_minutes: float = Field(default=10.0, gt=0.0)
    account_proxy_size_factor: float = Field(default=0.25, gt=0.0, le=1.0)
    account_proxy_soft_unwind_limit: float = Field(default=150.0, gt=0.0)
    account_proxy_position_diff_threshold: float = Field(default=1.0, ge=0.0)

    # RESIDUAL-TAKER-001 shares MAKE's process state but has separate RISK
    # attribution. It is opt-in and constrained to the explicit live universe.
    residual_taker_enabled: bool = False
    residual_taker_shadow_only: bool = True
    residual_taker_size: int = Field(default=50, gt=0, le=2_147_483_647)
    residual_taker_exchange_ids: str = ""
    residual_taker_max_pm_book_age_ms: int = Field(default=35_000, gt=0)
    residual_taker_max_position: int | None = Field(default=None, gt=0)

    # SHADOW-002 is disabled by default and has no order-write capability.
    shadow_enabled: bool = False
    shadow_journal_path: Path = Path("data/shadow_002/events.jsonl")
    shadow_candidate_queue_capacity: int = Field(default=512, gt=0, le=100_000)
    shadow_ingress_queue_capacity: int = Field(default=4096, gt=0, le=1_000_000)
    shadow_persistence_queue_capacity: int = Field(default=65_536, gt=0, le=2_000_000)
    shadow_persistence_batch_size: int = Field(default=256, gt=0, le=10_000)
    shadow_candidate_timeout_ms: int = Field(default=50, gt=0, le=60_000)
    shadow_capture_mirror_enabled: bool = True
    shadow_snapshot_min_interval_seconds: float = Field(default=1.0, ge=0.0, le=60.0)
    # Explicit only: no post-hoc 005F grid origin is inferred from observed outcomes.
    shadow_005f_grid_origin: datetime | None = None

    # LIVE-LEARN-001 consumes SHADOW's durable decision stream. It never writes orders.
    live_learn_enabled: bool = False
    live_learn_outcome_path: Path = Path("data/live_learn/outcomes.jsonl")
    live_learn_report_path: Path = Path("data/live_learn/reports")
    live_learn_queue_capacity: int = Field(default=200_000, ge=1_000, le=2_000_000)
    live_learn_max_retained_decisions: int = Field(default=20_000, ge=1, le=500_000)
    live_learn_evidence_grace_seconds: float = Field(default=5.0, ge=0.0, le=300.0)
    live_learn_max_evidence_age_seconds: float = Field(default=15.0, gt=0.0, le=300.0)

    # MODEL-RUNTIME-001 startup allowlists.  Values are parsed/frozen once by
    # the model runtime; they are never read on the model evaluation hot path.
    model_paper_ids: str = ""
    model_live_ids: str = ""

    sig_realtime_storage_path: Path = Path("data/sig_realtime.sqlite3")
    sig_research_path: Path = Path("data/sig_research")
    sig_capture_queue_max: int = Field(default=16_384, ge=10_000, le=65_536)
    sig_capture_parquet_shard_seconds: int = Field(default=60, ge=10, le=300)
    sig_capture_parquet_max_rows_per_shard: int = Field(default=5_000, ge=1_000, le=1_000_000)
    sig_realtime_book_depth: int = Field(default=20, ge=1, le=200)
    sig_realtime_tracked_exchange_ids: str = ""
    # Exchanges MAKE keeps fresh SIG marks for (held inventory) without quoting.
    maker_mark_only_exchange_ids: str = ""
    sig_rest_governor_rate_per_second: float = Field(default=2.0, gt=0.0, le=100.0)
    # SIG rate limits are per account across all API keys. When a path is set,
    # every process on the host also draws from one flock-guarded token bucket
    # at this combined rate; HIGH (execution) priority keeps a reserved token.
    sig_rest_account_budget_path: Path | None = None
    sig_rest_account_rate_per_second: float = Field(default=3.0, gt=0.0, le=100.0)
    sig_rest_shared_cooldown_max_seconds: float = Field(default=8.0, ge=0.5, le=120.0)
    sig_realtime_open_book_refresh_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    sig_realtime_bulk_price_refresh_seconds: float = Field(default=10.0, ge=1.0, le=300.0)
    sig_realtime_token_refresh_margin_seconds: float = Field(default=300.0, ge=30, le=1800)
    sig_realtime_retention_days: int = Field(default=14, ge=1, le=90)

    polymarket_capture_enabled: bool = False
    polymarket_gamma_base_url: AnyHttpUrl = AnyHttpUrl("https://gamma-api.polymarket.com")
    polymarket_clob_base_url: AnyHttpUrl = AnyHttpUrl("https://clob.polymarket.com")
    polymarket_ws_url: AnyUrl = AnyUrl("wss://ws-subscriptions-clob.polymarket.com/ws/market")
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
    polymarket_capture_mapping_path: Path | None = None

    @field_validator(
        "environment",
        "tournament_id",
        "tournament_slug",
        "risk_profile_name",
        "risk_profile_version",
    )
    @classmethod
    def reject_blank_optional_strings(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("configuration string must not be blank")
        return value

    @field_validator(
        "polymarket_storage_path",
        "polymarket_research_path",
        "sig_realtime_storage_path",
        "sig_research_path",
        "execution_journal_path",
        "risk_state_path",
        "maker_mapping_path",
        "shadow_journal_path",
        "live_learn_outcome_path",
        "live_learn_report_path",
    )
    @classmethod
    def reject_blank_storage_path(cls, value: Path) -> Path:
        if not str(value).strip():
            raise ValueError("polymarket_storage_path must not be blank")
        return value

    @field_validator("risk_exposure_groups_path")
    @classmethod
    def reject_blank_optional_path(cls, value: Path | None) -> Path | None:
        if value is not None and not str(value).strip():
            raise ValueError("risk_exposure_groups_path must not be blank")
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
        if self.maker_max_bbo_age_ms < int(self.sig_realtime_bulk_price_refresh_seconds * 1_000):
            raise ValueError(
                "maker_max_bbo_age_ms must cover the configured SIG bulk-price refresh interval"
            )
        if self.maker_require_trusted_depth and self.maker_max_depth_age_ms < int(
            self.sig_realtime_open_book_refresh_seconds * 1_000
        ):
            raise ValueError(
                "maker_max_depth_age_ms must cover the configured trusted-depth refresh interval"
            )
        return self

    @model_validator(mode="after")
    def validate_risk002_configuration(self) -> Self:
        state_dependent = (
            self.risk_max_per_strategy_exposure,
            self.risk_max_event_group_exposure,
            self.risk_max_tournament_exposure,
            self.risk_session_loss_limit,
            self.risk_drawdown_limit,
        )
        if (
            any(value is not None for value in state_dependent)
            and not self.risk_capital_control_enabled
        ):
            raise ValueError(
                "RISK-002 state-dependent caps require risk_capital_control_enabled=true"
            )
        if self.risk_capital_control_enabled and self.risk_max_mark_age_ms < int(
            self.sig_realtime_bulk_price_refresh_seconds * 1_000
        ):
            raise ValueError("risk_max_mark_age_ms must cover SIG bulk-price refresh interval")
        return self

    @model_validator(mode="after")
    def validate_exploratory_risk_profile(self) -> Self:
        if self.risk_profile_mode != "EXPLORATORY":
            return self
        global_caps = (
            self.risk_max_order_size,
            self.risk_max_gross_exposure,
            self.risk_max_per_market_exposure,
            self.risk_max_open_order_exposure,
            self.risk_max_concurrent_open_orders,
        )
        exploratory_caps = (
            self.risk_exploratory_max_order_size,
            self.risk_exploratory_max_gross_exposure,
            self.risk_exploratory_max_per_market_exposure,
            self.risk_exploratory_max_open_order_exposure,
            self.risk_exploratory_max_concurrent_open_orders,
        )
        if any(value is None for value in global_caps):
            raise ValueError("EXPLORATORY risk requires complete global hard caps")
        if any(value is None for value in exploratory_caps):
            raise ValueError("EXPLORATORY risk requires complete exploratory caps")
        return self

    @model_validator(mode="after")
    def validate_shadow_configuration(self) -> Self:
        if self.shadow_enabled and not self.maker_enabled:
            raise ValueError("shadow_enabled requires maker_enabled=true")
        if self.live_learn_enabled and not self.shadow_enabled:
            raise ValueError("live_learn_enabled requires shadow_enabled=true")
        models_configured = self.model_paper_ids.strip() or self.model_live_ids.strip()
        if models_configured and not self.shadow_enabled:
            raise ValueError("configured models require shadow_enabled=true")
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
            if not self.risk_capital_control_enabled:
                raise ValueError("LIVE execution requires risk_capital_control_enabled=true")
            if self.risk_session_loss_limit is None:
                raise ValueError("LIVE execution requires explicit risk_session_loss_limit")
            if self.risk_drawdown_limit is None:
                raise ValueError("LIVE execution requires explicit risk_drawdown_limit")
            limits = (
                self.risk_max_order_size,
                self.risk_max_gross_exposure,
                self.risk_max_per_market_exposure,
                self.risk_max_open_order_exposure,
                self.risk_max_concurrent_open_orders,
            )
            if any(value is None for value in limits):
                raise ValueError("LIVE execution requires every central risk cap")
            if self.risk_max_tournament_exposure is None:
                raise ValueError("LIVE execution requires explicit risk_max_tournament_exposure")
            if (
                self.risk_max_gross_exposure is not None
                and self.risk_max_tournament_exposure > self.risk_max_gross_exposure
            ):
                raise ValueError(
                    "LIVE risk_max_tournament_exposure cannot exceed risk_max_gross_exposure"
                )
            if self.risk_profile_mode == "EXPLORATORY":
                if self.risk_exploratory_max_tournament_exposure is None:
                    raise ValueError(
                        "LIVE EXPLORATORY requires explicit "
                        "risk_exploratory_max_tournament_exposure"
                    )
                if (
                    self.risk_exploratory_max_gross_exposure is not None
                    and self.risk_exploratory_max_tournament_exposure
                    > self.risk_exploratory_max_gross_exposure
                ):
                    raise ValueError(
                        "LIVE exploratory tournament exposure cannot exceed "
                        "exploratory gross exposure"
                    )
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
            "risk_max_per_strategy_exposure": self.risk_max_per_strategy_exposure,
            "risk_max_event_group_exposure": self.risk_max_event_group_exposure,
            "risk_max_tournament_exposure": self.risk_max_tournament_exposure,
            "risk_session_loss_limit": self.risk_session_loss_limit,
            "risk_drawdown_limit": self.risk_drawdown_limit,
            "risk_max_state_age_ms": self.risk_max_state_age_ms,
            "risk_max_account_age_ms": self.risk_max_account_age_ms,
            "risk_max_mark_age_ms": self.risk_max_mark_age_ms,
            "risk_capital_control_enabled": self.risk_capital_control_enabled,
            "risk_state_path": str(self.risk_state_path),
            "risk_exposure_groups_path": (
                None
                if self.risk_exposure_groups_path is None
                else str(self.risk_exposure_groups_path)
            ),
            "risk_profile_name": self.risk_profile_name,
            "risk_profile_version": self.risk_profile_version,
            "risk_profile_mode": self.risk_profile_mode,
            "risk_exploratory_max_order_size": self.risk_exploratory_max_order_size,
            "risk_exploratory_max_gross_exposure": (self.risk_exploratory_max_gross_exposure),
            "risk_exploratory_max_per_market_exposure": (
                self.risk_exploratory_max_per_market_exposure
            ),
            "risk_exploratory_max_open_order_exposure": (
                self.risk_exploratory_max_open_order_exposure
            ),
            "risk_exploratory_max_concurrent_open_orders": (
                self.risk_exploratory_max_concurrent_open_orders
            ),
            "risk_exploratory_max_per_strategy_exposure": (
                self.risk_exploratory_max_per_strategy_exposure
            ),
            "risk_exploratory_max_event_group_exposure": (
                self.risk_exploratory_max_event_group_exposure
            ),
            "risk_exploratory_max_tournament_exposure": (
                self.risk_exploratory_max_tournament_exposure
            ),
            "maker_enabled": self.maker_enabled,
            "maker_mapping_path": str(self.maker_mapping_path),
            "maker_max_abs_inventory": self.maker_max_abs_inventory,
            "maker_base_size": self.maker_base_size,
            "maker_minimum_size": self.maker_minimum_size,
            "maker_base_half_spread_ticks": self.maker_base_half_spread_ticks,
            "maker_inventory_risk_aversion": self.maker_inventory_risk_aversion,
            "maker_uncertainty_multiplier": self.maker_uncertainty_multiplier,
            "maker_volatility_multiplier": self.maker_volatility_multiplier,
            "maker_toxicity_half_spread_ticks": self.maker_toxicity_half_spread_ticks,
            "maker_max_bbo_age_ms": self.maker_max_bbo_age_ms,
            "maker_max_fv_age_ms": self.maker_max_fv_age_ms,
            "maker_max_account_age_ms": self.maker_max_account_age_ms,
            "account_proxy_enabled": self.account_proxy_enabled,
            "account_proxy_max_age_minutes": self.account_proxy_max_age_minutes,
            "account_proxy_size_factor": self.account_proxy_size_factor,
            "account_proxy_soft_unwind_limit": self.account_proxy_soft_unwind_limit,
            "account_proxy_position_diff_threshold": (self.account_proxy_position_diff_threshold),
            "maker_max_inventory_age_ms": self.maker_max_inventory_age_ms,
            "maker_max_signal_age_ms": self.maker_max_signal_age_ms,
            "maker_require_trusted_depth": self.maker_require_trusted_depth,
            "maker_max_depth_age_ms": self.maker_max_depth_age_ms,
            "maker_min_replace_ticks": self.maker_min_replace_ticks,
            "maker_min_replace_size": self.maker_min_replace_size,
            "maker_min_requote_interval_ms": self.maker_min_requote_interval_ms,
            "shadow_enabled": self.shadow_enabled,
            "shadow_journal_path": str(self.shadow_journal_path),
            "shadow_candidate_queue_capacity": self.shadow_candidate_queue_capacity,
            "shadow_ingress_queue_capacity": self.shadow_ingress_queue_capacity,
            "shadow_persistence_queue_capacity": self.shadow_persistence_queue_capacity,
            "shadow_persistence_batch_size": self.shadow_persistence_batch_size,
            "shadow_candidate_timeout_ms": self.shadow_candidate_timeout_ms,
            "shadow_capture_mirror_enabled": self.shadow_capture_mirror_enabled,
            "shadow_snapshot_min_interval_seconds": (self.shadow_snapshot_min_interval_seconds),
            "live_learn_enabled": self.live_learn_enabled,
            "live_learn_outcome_path": str(self.live_learn_outcome_path),
            "live_learn_report_path": str(self.live_learn_report_path),
            "live_learn_queue_capacity": self.live_learn_queue_capacity,
            "live_learn_max_retained_decisions": (self.live_learn_max_retained_decisions),
            "live_learn_evidence_grace_seconds": self.live_learn_evidence_grace_seconds,
            "live_learn_max_evidence_age_seconds": (self.live_learn_max_evidence_age_seconds),
            "model_paper_id_count": len(
                {value.strip() for value in self.model_paper_ids.split(",") if value.strip()}
            ),
            "model_live_id_count": len(
                {value.strip() for value in self.model_live_ids.split(",") if value.strip()}
            ),
            "sig_read_credential_configured": self.sig_read_credential is not None,
            "sig_trade_credential_configured": self.sig_trade_credential is not None,
            "sig_realtime_storage_path": str(self.sig_realtime_storage_path),
            "sig_research_path": str(self.sig_research_path),
            "sig_capture_queue_max": self.sig_capture_queue_max,
            "sig_capture_parquet_shard_seconds": self.sig_capture_parquet_shard_seconds,
            "sig_capture_parquet_max_rows_per_shard": (self.sig_capture_parquet_max_rows_per_shard),
            "sig_realtime_book_depth": self.sig_realtime_book_depth,
            "sig_realtime_tracked_exchange_count": len(
                {
                    value.strip()
                    for value in self.sig_realtime_tracked_exchange_ids.split(",")
                    if value.strip()
                }
            ),
            "sig_rest_governor_rate_per_second": self.sig_rest_governor_rate_per_second,
            "sig_rest_shared_cooldown_max_seconds": (self.sig_rest_shared_cooldown_max_seconds),
            "sig_realtime_open_book_refresh_seconds": (self.sig_realtime_open_book_refresh_seconds),
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
            "polymarket_capture_mapping_path": (
                str(self.polymarket_capture_mapping_path)
                if self.polymarket_capture_mapping_path is not None
                else None
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
