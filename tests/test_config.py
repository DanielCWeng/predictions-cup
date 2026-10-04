from __future__ import annotations

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from predictions_cup.config import AppSettings, load_settings


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
        "POLYMARKET_SUPERVISED_IDS",
        "POLYMARKET_RESEARCH_PATH",
        "MAKER_FILL_SEEKING_ENABLED",
        "MAKER_FILL_SEEKING_MIN_EDGE",
        "MAKER_DEEP_LADDER_ENABLED",
        "MAKER_DEEP_LADDER_LEVEL_OFFSETS",
        "MAKER_DEEP_LADDER_LEVEL_SIZES",
        "MAKER_DEEP_LADDER_POSITION_CAP",
        "RESIDUAL_TAKER_THRESHOLD",
        "RESIDUAL_TAKER_PM_SPREAD_CAP",
        "RESIDUAL_TAKER_MIN_PM_DEPTH",
        "RESIDUAL_TAKER_COOLDOWN_SECONDS",
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
    assert settings.sig_realtime_retention_days == 3
    assert settings.sig_realtime_book_retention_days == 1
    assert settings.polymarket_supervised_ids == ""
    assert settings.polymarket_storage_path == Path("data/polymarket_operational.sqlite3")
    assert settings.polymarket_research_path == Path("data/polymarket_research")
    assert settings.live_learn_max_retained_decisions == 20_000
    assert settings.shadow_journal_max_bytes == 512 * 1024 * 1024
    assert settings.shadow_journal_max_files == 4
    assert settings.shadow_snapshot_min_interval_seconds == 60.0
    assert settings.sig_capture_parquet_max_rows_per_shard == 5_000
    assert settings.maker_fill_seeking_enabled is False
    assert settings.maker_fill_seeking_min_edge == 0.02
    assert settings.maker_deep_ladder_enabled is False
    assert settings.maker_deep_ladder_level_offsets == (0.01, 0.02, 0.04)
    assert settings.maker_deep_ladder_level_sizes == (50, 100, 150)
    assert settings.maker_deep_ladder_position_cap == 200
    assert settings.residual_taker_threshold == 0.02
    assert settings.residual_taker_pm_spread_cap == 0.02
    assert settings.residual_taker_min_pm_depth == 50.0
    assert settings.residual_taker_cooldown_seconds == 60.0


def test_fill_hunt_settings_load_from_environment(
    clean_config_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_config_env
    monkeypatch.setenv("PREDICTIONS_CUP_MAKER_FILL_SEEKING_ENABLED", "true")
    monkeypatch.setenv("PREDICTIONS_CUP_MAKER_FILL_SEEKING_MIN_EDGE", "0.015")
    monkeypatch.setenv("PREDICTIONS_CUP_RESIDUAL_TAKER_THRESHOLD", "0.01")
    monkeypatch.setenv("PREDICTIONS_CUP_RESIDUAL_TAKER_PM_SPREAD_CAP", "0.03")
    monkeypatch.setenv("PREDICTIONS_CUP_RESIDUAL_TAKER_MIN_PM_DEPTH", "75")
    monkeypatch.setenv("PREDICTIONS_CUP_RESIDUAL_TAKER_COOLDOWN_SECONDS", "15")

    settings = AppSettings()

    assert settings.maker_fill_seeking_enabled is True
    assert settings.maker_fill_seeking_min_edge == 0.015
    assert settings.maker_deep_ladder_enabled is False
    assert settings.maker_deep_ladder_position_cap == 200
    assert settings.residual_taker_threshold == 0.01
    assert settings.residual_taker_pm_spread_cap == 0.03
    assert settings.residual_taker_min_pm_depth == 75.0
    assert settings.residual_taker_cooldown_seconds == 15.0


def test_swing_accepted_mapping_path_loads(
    clean_config_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_config_env
    assert AppSettings().risk_swing_accepted_mapping_path is None
    monkeypatch.setenv(
        "PREDICTIONS_CUP_RISK_SWING_ACCEPTED_MAPPING_PATH",
        "data/mappings/sig_polymarket_2026.json",
    )

    settings = AppSettings()

    assert settings.risk_swing_accepted_mapping_path == Path(
        "data/mappings/sig_polymarket_2026.json"
    )


def test_deep_ladder_configuration_loads(
    clean_config_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_config_env
    monkeypatch.setenv("PREDICTIONS_CUP_MAKER_DEEP_LADDER_ENABLED", "true")
    monkeypatch.setenv("PREDICTIONS_CUP_MAKER_DEEP_LADDER_LEVEL_OFFSETS", "[0.01,0.02,0.04]")
    monkeypatch.setenv("PREDICTIONS_CUP_MAKER_DEEP_LADDER_LEVEL_SIZES", "[25,50,100]")
    monkeypatch.setenv("PREDICTIONS_CUP_MAKER_DEEP_LADDER_POSITION_CAP", "175")

    settings = AppSettings()

    assert settings.maker_deep_ladder_enabled is True
    assert settings.maker_deep_ladder_level_offsets == (0.01, 0.02, 0.04)
    assert settings.maker_deep_ladder_level_sizes == (25, 50, 100)
    assert settings.maker_deep_ladder_position_cap == 175


def test_maker_freshness_defaults_cover_sig_refresh_intervals(
    clean_config_env: None,
) -> None:
    del clean_config_env
    settings = AppSettings()

    assert settings.maker_max_bbo_age_ms >= int(
        settings.sig_realtime_bulk_price_refresh_seconds * 1_000
    )
    assert settings.maker_max_depth_age_ms >= int(
        settings.sig_realtime_open_book_refresh_seconds * 1_000
    )


def test_maker_rejects_freshness_windows_shorter_than_sig_refresh_contracts(
    clean_config_env: None,
) -> None:
    del clean_config_env
    with pytest.raises(ValidationError):
        AppSettings.model_validate(
            {
                "maker_max_bbo_age_ms": 1_000,
                "sig_realtime_bulk_price_refresh_seconds": 10.0,
            }
        )
    with pytest.raises(ValidationError):
        AppSettings.model_validate(
            {
                "maker_require_trusted_depth": True,
                "maker_max_depth_age_ms": 1_000,
                "sig_realtime_open_book_refresh_seconds": 30.0,
            }
        )


def test_tracked_depth_runtime_configuration_is_external_and_nonsecret(
    clean_config_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_config_env
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "PREDICTIONS_CUP_SIG_REALTIME_TRACKED_EXCHANGE_IDS",
        "exchange-a, exchange-b,exchange-a",
    )

    settings = AppSettings()

    assert settings.sig_realtime_tracked_exchange_ids == "exchange-a, exchange-b,exchange-a"
    assert settings.diagnostic_fields()["sig_realtime_tracked_exchange_count"] == 2


def test_supervised_polymarket_ids_are_external_and_counted(
    clean_config_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_config_env
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "PREDICTIONS_CUP_POLYMARKET_SUPERVISED_IDS",
        "condition-a, token-b,condition-a",
    )

    settings = AppSettings()

    assert settings.polymarket_supervised_ids == "condition-a, token-b,condition-a"
    assert settings.diagnostic_fields()["polymarket_supervised_id_count"] == 2


def test_runtime_env_only_disables_repo_dotenv(
    clean_config_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_config_env
    (tmp_path / ".env").write_text(
        "PREDICTIONS_CUP_SIG_TRADE_CREDENTIAL=DOTENV_TRADE_SECRET\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    settings = load_settings(use_dotenv=False)

    assert settings.sig_trade_credential is None
    assert settings.trading_enabled is False


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
