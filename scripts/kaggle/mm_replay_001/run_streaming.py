# ruff: noqa
"""Assemble and execute the pinned MM-REPLAY-001 streaming runner."""
from __future__ import annotations

import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
PARTS = tuple(HERE / f"run_streaming.part{i}.txt" for i in (1, 2, 3))
EXPECTED_SHA256 = "5baa9d90580c313aa6d5673511e767d3c44c6ea6622a35ae42d65355618e805e"

source = "".join(path.read_text(encoding="utf-8") for path in PARTS)
actual = hashlib.sha256(source.encode("utf-8")).hexdigest()
if actual != EXPECTED_SHA256:
    raise RuntimeError(
        f"MM-REPLAY-001 streaming source hash mismatch: {actual} != {EXPECTED_SHA256}"
    )
exec(compile(source, "run_streaming_assembled.py", "exec"), {"__name__": "__main__", "__file__": str(HERE / "run_streaming_assembled.py")})
