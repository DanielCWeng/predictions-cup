"""Fail-closed default for unproven live rotation adapters."""
from predictions_cup.config.settings import AppSettings


def test_live_rotation_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("PREDICTIONS_CUP_ROTATION_PASSIVE_RECYCLING_ENABLED", raising=False)
    monkeypatch.delenv("PREDICTIONS_CUP_ROTATION_ACTIVE_EXITS_ENABLED", raising=False)
    settings = AppSettings(_env_file=None)
    assert settings.rotation_passive_recycling_enabled is False
    assert settings.rotation_active_exits_enabled is False


def test_explicit_rotation_override_remains_available(monkeypatch):
    monkeypatch.setenv("PREDICTIONS_CUP_ROTATION_PASSIVE_RECYCLING_ENABLED", "true")
    monkeypatch.setenv("PREDICTIONS_CUP_ROTATION_ACTIVE_EXITS_ENABLED", "true")
    settings = AppSettings(_env_file=None)
    assert settings.rotation_passive_recycling_enabled is True
    assert settings.rotation_active_exits_enabled is True
