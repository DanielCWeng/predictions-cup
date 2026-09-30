# Decision 002 — Explicit structural-shadow Polymarket capture

**Status:** PROPOSED  
**Date:** 30 September 2026

## Context

The accepted production Polymarket recorder is strict include-only. Its canonical launch universe is
generated from the accepted `EXACT + DERIVED` SIG↔Polymarket crosswalk.

R3-FV-001M found no historical structural point-fair-value model worth promoting. The only surviving
use of several independent structural markets is **live shadow learning**: observe genuinely new
source shocks and measure subsequent SIG book response at 5s / 30s / 300s.

Those source markets are intentionally *not* SIG direct/derived mappings, so they are absent from the
accepted crosswalk and therefore absent from the current production capture universe. Without a
separate explicit allowlist, the shadow handoff cannot collect its own launch evidence.

## Decision

Keep mapping identity and research capture identity separate.

The strict supervised Polymarket universe is the union of:

1. accepted `EXACT + DERIVED` mapped token IDs from
   `data/mappings/sig_polymarket_2026.json`; and
2. the explicit YES-token allowlist in
   `data/capture/r3_live_shadow_polymarket_ids.json`.

The shadow allowlist currently contains 13 tokens:

- 11 Republican U.S. Senate seat-count contracts;
- the “Democrats win all core four Senate races” contract;
- the “Republicans win any Biden-Trump Senate/Governor election” contract.

These are capture-only research sources. They do not become mappings, synthetic equivalents,
fair-value identities, or tradeable production signals by inclusion.

Generate the runtime value with:

```bash
.venv/bin/python -m predictions_cup.external.polymarket.supervised_universe   > /tmp/polymarket_supervised_ids.txt
```

Then set the single emitted CSV line as:

```text
PREDICTIONS_CUP_POLYMARKET_SUPERVISED_IDS=<generated CSV>
```

The generator emits counts to stderr and the ID CSV to stdout. The recorder remains
`--require-explicit-universe` and fail-closed for unresolved identities.

## Safety / scientific boundaries

- DATA-001 is not used.
- No shadow source is inserted into the accepted mapping document.
- Only explicit YES token IDs are added; no broad election heuristic is enabled.
- Trading remains disabled by the capture service.
- Historical R3 evidence remains negative/non-promotional.
- Live shadow evidence must pass the frozen R3 live gate before any micro-size challenger use.
- Removing the shadow allowlist returns exactly to the accepted mapping-bounded universe.

## Operational consequence

The launch Polymarket subscriber grows by 13 token subscriptions relative to the mapping-only
universe. The existing recorder schema, storage layout, systemd unit and fail-closed strict selector
do not change.

A production-shaped smoke must confirm that all generated IDs resolve, the subscribed token count
equals the generated ID count, and storage/queue health remains normal.


## Live source validation — 30 September 2026

A read-only public smoke at GitHub Actions run `36687130088` validated the exact capture-only
source set against current Polymarket:

- Gamma explicit lookups: **13 / 13 markets active and open**;
- strict selector: **13 / 13 expected YES tokens recovered**;
- public CLOB `/books`: **13 / 13 books returned**;
- missing tokens: **0**.

Machine-readable evidence is frozen in
`data/capture/r3_live_shadow_smoke_evidence.json`.

This validates current source identity/book availability only. It does not replace the launch-host
702-token systemd soak, storage-health checks, or the R3 live signal gate.
