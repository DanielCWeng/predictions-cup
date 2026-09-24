"""Runtime configuration with fail-closed secret handling."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import AnyHttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


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

    @field_validator("environment", "tournament_id", "tournament_slug")
    @classmethod
    def reject_blank_optional_strings(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("configuration string must not be blank")
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

    def diagnostic_fields(self) -> dict[str, str | bool | None]:
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
        }


def load_settings() -> AppSettings:
    """Load settings on demand; configuration is never module-global state."""
    return AppSettings()
