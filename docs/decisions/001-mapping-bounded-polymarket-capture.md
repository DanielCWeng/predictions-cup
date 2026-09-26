# Decision 001 — Mapping-bounded Polymarket capture

**Status:** ACCEPTED  
**Date:** 26 September 2026

## Context

The public Polymarket recorder originally used a broad 2026 U.S. election heuristic. During live
BUILD-007 testing that selector discovered 3,160 markets / 6,320 tokens.

The competition does not require a generic always-on election firehose. The production research
question is the relationship between the live SIG tournament markets and their actual Polymarket
counterparts. The observed SIG tournament contained roughly 237 exchanges; the exact external
market/token count depends on the accepted crosswalk.

Broad Gamma discovery can still be useful for metadata and mapping, but broad 1 Hz capture creates
unnecessary subscription, storage and operational load.

## Decision

The supervised Polymarket service is strict include-only.

- Production IDs come from LIVE-MAPPING-GATE-001.
- IDs may be market IDs, condition IDs or token IDs.
- Market/condition IDs select their aligned tokens; a token ID selects only that token.
- Every configured ID must resolve against an active/non-closed Gamma market.
- Missing or unresolved supervised IDs fail closed.
- The systemd service never falls back to the broad election heuristic.
- No Cup market IDs are hard-coded into repository code or unit files.

The broad heuristic remains an explicit/manual research capability, not the always-on Cup lane.

## Consequences

- The production capture universe cannot be finalized before the live mapping gate is accepted.
- Temporary public IDs used for runtime smoke tests must never be promoted into production config.
- Mapping acceptance is now a direct dependency of production paired capture.
- Metadata discovery and high-frequency subscription scope are deliberately separate concepts.
