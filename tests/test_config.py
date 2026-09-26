from __future__ import annotations

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from predictions_cup.config import AppSettings


@pytest.fixture
def clean_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    names = (
        "ENVIRONMENT",
        "LOG_LEVEL",
        "SIG_API_BASE_URL",
        "SIG_READ_CREDENTIAL",
        "SIG_TRADE_CREDENTIAL",
        "TOURNAMENT_ID",
        "TOURNAMENT_SLUG",
        "TRADING_ENABLED",
        "SIG_REST_GOVERNOR_RATE_PER_SECOND",
        "SIG_REST_SHARED_COOLDOWN_MAX_SECONDS",
        "SIG_REALTIME_BULK_PRICE_REFRESH_SECONDS",
        "SIG_REALTIME_TRACKED_EXCHANGE_IDS",
    )
    for name in names:
        monkeypatch.delenv(f"PREDICTIONS_CUP_{name}", raising=False)


def test_defaults_load_without_credentials(
    clean_config_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_config_env
    monkeypatch.chdir(tmp_path)

    settings = AppSettings()

    assert settings.sig_read_credential is None
    assert settings.sig_trade_credential is None
    assert settings.trading_enabled is False
    assert str(settings.sig_api_base_url) == "https://www.thesuper.market/api/v1"
    assert settings.sig_rest_governor_rate_per_second == 2.0
    assert settings.sig_realtime_bulk_price_refresh_seconds == 10.0
    assert settings.sig_realtime_tracked_exchange_ids == ""


def test_tracked_depth_runtime_configuration_is_external_and_nonsecret(
    clean_config_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_config_env
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PREDICTIONS_CUP_SIG_REALTIME_TRACKED_EXCHANGE_IDS", "exchange-a, exchange-b,exchange-a")

    settings = AppSettings()

    assert settings.sig_realtime_tracked_exchange_ids == "exchange-a, exchange-b,exchange-a"
    assert settings.diagnostic_fields()["sig_realtime_tracked_exchange_count"] == 2


def test_runtime_environment_overrides_local_dotenv(
    clean_config_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_config_env
    env_file = tmp_path / ".env"
    env_file.write_text("PREDICTIONS_CUP_LOG_LEVEL=DEBUG\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PREDICTIONS_CUP_LOG_LEVEL", "error")

    settings = AppSettings()

    assert settings.log_level == "ERROR"


def test_secrets_are_redacted_from_normal_representations(
    clean_config_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_config_env
    read_value = "TEST_READ_SECRET_DO_NOT_LEAK"
    trade_value = "TEST_TRADE_SECRET_DO_NOT_LEAK"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PREDICTIONS_CUP_SIG_READ_CREDENTIAL", read_value)
    monkeypatch.setenv("PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL", trade_value)

    settings = AppSettings()

    record = logging.LogRecord(
        "secret-regression",
        logging.INFO,
        __file__,
        1,
        "settings=%s",
        (settings,),
        None,
    )
    representations = (
        repr(settings),
        str(settings),
        settings.model_dump_json(),
        str(settings.model_dump()),
        record.getMessage(),
        str(settings.diagnostic_fields()),
    )

    for representation in representations:
        assert read_value not in representation
        assert trade_value not in representation


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sig_api_base_url", "not-a-url"),
        ("log_level", "VERBOSE"),
        ("sig_read_credential", "   "),
        ("sig_trade_credential", ""),
        ("environment", " "),
        ("sig_rest_governor_rate_per_second", "0"),
        ("sig_rest_shared_cooldown_max_seconds", "0"),
        ("sig_realtime_bulk_price_refresh_seconds", "0"),
    ],
)
def test_materially_invalid_configuration_is_rejected(
    clean_config_env: None,
    field: str,
    value: str,
) -> None:
    del clean_config_env
    with pytest.raises(ValidationError):
        AppSettings.model_validate({field: value})


def test_trading_enablement_fails_closed_without_trade_credential(
    clean_config_env: None,
) -> None:
    del clean_config_env
    with pytest.raises(ValidationError):
        AppSettings.model_validate({"trading_enabled": True})
