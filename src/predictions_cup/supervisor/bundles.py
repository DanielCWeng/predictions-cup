"""Compact forensic bundles for external Claude/Codex analysis."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from predictions_cup.supervisor.contracts import (
    BUNDLE_SCHEMA_VERSION,
    SupervisorSnapshot,
)
from predictions_cup.supervisor.persistence import atomic_json


class BundleWriter:
    def __init__(self, root: Path) -> None:
        self.root = root

    def emit(
        self,
        *,
        bundle_type: str,
        snapshot: SupervisorSnapshot,
        trigger_codes: tuple[str, ...] = (),
        previous_bundle_id: str | None = None,
    ) -> Path:
        normalized = bundle_type.lower()
        if normalized not in {"hourly", "events"}:
            raise ValueError("bundle_type must be hourly or events")
        stamp = snapshot.observed_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        bundle_id = (
            f"{normalized}-{stamp}-{snapshot.snapshot_id.rsplit('-', 1)[-1][:10]}"
        )
        directory = self.root / "bundles" / normalized / bundle_id
        directory.mkdir(parents=True, exist_ok=False)

        files: dict[str, Mapping[str, object]] = {
            "supervisor.json": snapshot.to_dict(),
        }
        for name in (
            "observe",
            "risk",
            "execution",
            "shadow",
            "live_learn",
            "sig_capture",
            "polymarket_capture",
            "system",
            "clock",
        ):
            section = snapshot.sections.get(name)
            files[f"{name}.json"] = (
                {str(key): value for key, value in section.items()}
                if isinstance(section, dict)
                else {"available": False}
            )

        hashes: dict[str, str] = {}
        for name, payload in files.items():
            path = directory / name
            atomic_json(path, payload)
            hashes[name] = _sha256(path)

        manifest: dict[str, object] = {
            "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
            "bundle_id": bundle_id,
            "bundle_type": normalized,
            "created_at": datetime.now(UTC).isoformat(),
            "trigger_codes": list(trigger_codes),
            "host_id": snapshot.host_id,
            "host_role": snapshot.host_role.value,
            "git_head": snapshot.git_head,
            "supervisor_snapshot_id": snapshot.snapshot_id,
            "source_ages": {
                source.source_id: source.age_seconds for source in snapshot.sources
            },
            "files": hashes,
            "previous_bundle_id": previous_bundle_id,
            "analysis_requested": True,
        }
        atomic_json(directory / "manifest.json", manifest)
        return directory


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
