# Rotation evidence + two-phase execution gate

Status: **draft, not live**. Source in this branch is intentionally separate from West's newer, dirty working tree. The West-only `liquidity_allocator.py` and `capital_rotation.py` are not currently tracked by GitHub `main`. Do not merge blindly or restart West on this PR alone.

## Application to live West

The allocator should call `RotationEvidenceCache.offer(..., publish=logger)` when a qualifying candidate first appears, rather than only printing `rank()` on the 60-second heartbeat. This captures transient opportunities without performing disk writes per tick; pass a normal INFO logger as callback. Bound logging with `offer` deduplication.

Use `update_account` on each authoritative account state: if trust fails, no active candidate survives. A confirmed position change invalidates all active candidates; routine open-order changes invalidate only pairs referencing the affected exchange. Evidence history is *diagnostic only*, never execution authority.

`TwoPhaseRotation` defaults to `live_enabled=False`, max 100 shares and maximum 100 SUSQies estimated entry collateral. Production adapters must obtain fresh exit and replacement books from the existing SIG data sources, validate signed positions and limits through RISK-002, send both operations through BUILD-009 idempotent journaling, cancel/reconcile exit remainder, then obtain a NEW authoritative account version. The second leg can never execute before a strictly smaller signed position and zero open/uncertain orders are proven. Use distinct logical IDs `<operation>:exit` and `<operation>:entry`.

If the exit partially fills, revalidate and replace only the confirmed released quantity. If the market or expected improvement disappears, keep the released exposure free. If any step is uncertain, halt and hand off to reconciliation, never retry the same logical operation optimistically.

This code is a safety primitive, not a complete trading strategy: external callback implementations and end-to-end replay/recovery tests are required before enabling live rotation. In particular, unresolved maker cancellation timeouts and the gross-cap bottleneck still require explicit care. Do not increase hard risk caps to force trades.
