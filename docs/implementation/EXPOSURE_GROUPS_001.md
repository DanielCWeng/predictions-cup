# EXPOSURE-GROUPS-001 — explicit semantic risk groups

This registry is the data/config side of RISK-002's existing `ExposureGroupProvider`; it does not add a risk engine.

Authoritative source: `data/mappings/sig_polymarket_2026.json`.

Rules:
- every SIG market receives the explicit tournament group from `sig_tournament_id`;
- a market receives `pm-event:<event_id>` only when the accepted mapping itself carries that Polymarket event ID, either on the direct mapping or an accepted derived component;
- titles are never used to infer a hard relationship;
- markets without an accepted event identity remain tournament-only and are counted explicitly in coverage.

Current coverage is 237/237 tournament-grouped and 225/237 with at least one accepted event group. The registry's `content_sha256` hashes the canonical compact JSON serialization of the `memberships` array.

RISK-002 can consume the file immediately through `load_exposure_group_provider()`; extra provenance fields are retained for auditability and ignored by the thin loader.
