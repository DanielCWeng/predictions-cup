"""Minimal non-trading application lifecycle."""

from __future__ import annotations

import logging

from predictions_cup.config import AppSettings, load_settings

LOGGER_NAME = "predictions_cup.app"


def configure_logging(settings: AppSettings) -> None:
    """Configure lightweight local logging from validated settings."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        force=True,
    )


def run(*, smoke_test: bool = False) -> int:
    """Load configuration, report safe state, and exit without external I/O."""
    settings = load_settings()
    configure_logging(settings)
    logger = logging.getLogger(LOGGER_NAME)
    mode = "smoke-test" if smoke_test else "one-shot"
    logger.info(
        "event=startup mode=%s phase=FOUNDATION_DOMAIN environment=%s "
        "trading_enabled=%s trading_capability=NONE",
        mode,
        settings.environment,
        settings.trading_enabled,
    )
    logger.info("event=shutdown reason=foundation-shell-complete")
    return 0
