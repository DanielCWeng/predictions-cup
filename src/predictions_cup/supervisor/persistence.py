"""Small atomic/append-only persistence for SUPERVISOR-001."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC
from pathlib import Path

from predictions_cup.supervisor.contracts import SupervisorSnapshot


def atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ) + "\n"
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def append_jsonl(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


class SupervisorStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.latest_path = root / "latest.json"
        self.events_path = root / "events.jsonl"
        self.actions_path = root / "actions.jsonl"

    def publish_latest(self, snapshot: SupervisorSnapshot) -> None:
        atomic_json(self.latest_path, snapshot.to_dict())

    def append_snapshot(self, snapshot: SupervisorSnapshot) -> Path:
        day = snapshot.observed_at.astimezone(UTC).strftime("%Y/%m/%d")
        path = self.root / "snapshots" / f"{day}.jsonl"
        append_jsonl(path, snapshot.to_dict())
        return path

    def append_event(self, payload: Mapping[str, object]) -> None:
        append_jsonl(self.events_path, payload)

    def append_action(self, payload: Mapping[str, object]) -> None:
        append_jsonl(self.actions_path, payload)


def _fsync_directory(path: Path) -> None:
    flags = getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, os.O_RDONLY | flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
