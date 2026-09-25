from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from predictions_cup.config import AppSettings


def test_polymarket_capture_defaults_are_safe_and_research_oriented() -> None:
    settings = AppSettings()

    assert settings.polymarket_capture_enabled is False
    assert settings.polymarket_snapshot_interval_seconds == 1.0
    assert settings.polymarket_book_depth == 20
    assert settings.polymarket_storage_path == Path("data/polymarket_capture.sqlite3")
    assert str(settings.polymarket_ws_url).startswith("wss://")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("polymarket_snapshot_interval_seconds", 0),
        ("polymarket_book_depth", 0),
        ("polymarket_gamma_page_limit", 0),
        ("polymarket_gamma_refresh_seconds", 1),
    ],
)
def test_invalid_polymarket_capture_settings_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        AppSettings.model_validate({field: value})
