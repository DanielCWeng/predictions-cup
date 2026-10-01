"""Process-level guard preventing duplicate LIVE MAKE instances."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import TextIO


class MakerInstanceLock:
    """Advisory OS lock beside the execution journal.

    The lock file may persist across crashes; the kernel lock does not. This
    makes stale-file recovery deterministic without a distributed lock service.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: TextIO | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            raise RuntimeError("MAKE instance lock is already held by this process")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RuntimeError(
                f"another LIVE MAKE instance holds {self.path}"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        self._handle = handle

    def close(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
