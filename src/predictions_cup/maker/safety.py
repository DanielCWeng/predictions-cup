"""Simple one-way process kill latch for MAKE-001."""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MakerKillSwitch:
    """One-way latch. Clearing requires process restart/reconstruction.

    This is intentionally boring. Any operator/control path may call activate();
    normal code cannot accidentally toggle it back off.
    """

    _active: bool = False
    _reason: str = ""

    @property
    def active(self) -> bool:
        return self._active

    @property
    def reason(self) -> str:
        return self._reason

    def activate(self, reason: str) -> None:
        if not reason.strip():
            raise ValueError("kill-switch reason must not be blank")
        newly_latched = not self._active
        self._active = True
        if not self._reason:
            self._reason = reason
        logger.warning("MAKE kill switch activation requested reason=%s", reason)
        if newly_latched:
            logger.warning("MAKE kill=True latch transition reason=%s", self._reason)
