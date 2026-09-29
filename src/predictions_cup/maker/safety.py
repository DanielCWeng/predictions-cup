"""Simple one-way process kill latch for MAKE-001."""

from __future__ import annotations

from dataclasses import dataclass


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
        self._active = True
        if not self._reason:
            self._reason = reason
