"""Minimal application lifecycle for the repository foundation."""

from __future__ import annotations

import logging
import os

LOGGER_NAME = "predictions_cup.app"


def configure_logging() -> None:
    """Configure lightweight local logging without exposing secrets."""
    level_name = os.getenv("PREDICTIONS_CUP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        force=True,
    )


def run(*, smoke_test: bool = False) -> int:
    """Start the non-trading application shell and exit cleanly."""
    configure_logging()
    logger = logging.getLogger(LOGGER_NAME)
    mode = "smoke-test" if smoke_test else "one-shot"
    logger.info(
        "event=startup mode=%s phase=FOUNDATION trading_capability=NONE state=unconfigured",
        mode,
    )
    logger.info("event=shutdown reason=foundation-shell-complete")
    return 0
