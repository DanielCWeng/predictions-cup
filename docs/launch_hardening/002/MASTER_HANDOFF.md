# LAUNCH-HARDENING-002 — MASTER HANDOFF

## Scope

- **Issue:** #110
- **Related fill-evidence issue:** #104
- **Frozen base:** `4a040a7d309df26098af5e81e96c7f1108d03117`
- **Branch:** `build/launch-hardening-002-lifecycle-reconciliation`
- **Real SIG orders sent:** **NO**
- **External compute used:** none
- **Other active implementation branches inspected/cherry-picked:** none

This lane is a deterministic production-shaped execution/account/risk torture test. It composes accepted repository contracts with synthetic venue/account adapters; it does not create a replacement execution architecture.

## Owned invariant

> Once economic intent is durably admitted, placement/cancel/reconnect/restart evidence must converge exactly once to authoritative economic truth. Anything not yet knowable remains risk-bearing and explicitly untrusted/incomplete.

The independent oracle owns only:

- cash;
- signed inventory;
- gross exposure;
- open-order exposure;
- uncertain exposure;
- filled/remaining quantity;
- explicitly known fees;
- order identity;
- operation identity.

The production implementation is never used to derive oracle expectations.

## Primary composed path

`tests/test_launch_hardening_002.py::test_composed_lifecycle_oracle_restart_and_reconciliation`

The test drives the real contracts:

```text
strategy Opportunity
→ central evaluate_risk
→ build_execution_plan
→ ExecutionReservationBook
→ ExecutionJournal
→ SigLiveSink
→ synthetic venue ACK/order IDs
→ AccountRealtimeController / AccountRealtimeStateEngine
→ provisional realtime fill
→ uncertain cancel
→ late fill
→ authoritative account snapshot
→ process destruction
→ ExecutionJournal reopen
→ recover_startup
→ authoritative cancel/fill reconciliation
→ central Risk
→ JournalExecutionEvidenceProvider
```

### Stage expectations

| Stage | Required truth |
| --- | --- |
| 0 clean startup | empty authoritative account; zero residuals; Risk healthy |
| 1 two-sided quote | Risk approves; both legs reserved; journal SUBMISSION is durable before synthetic network dispatch |
| 2 ACK/open | venue IDs 101/102 bind to original per-intent identity; known own order evidence does not revoke trust |
| 3 partial fill | +4 BUY changes independent cash/inventory once; account becomes untrusted pending authoritative reconciliation; learner shows provisional 4, never supported zero |
| 4 cancel/disconnect | cancel 101 becomes UNCERTAIN; reservation/exposure remains conservative |
| 5 late fill | +2 late BUY is retained and attributed to placement leg, never stolen by cancel operation |
| 6 reconnect | authoritative +6 position / open ask 102 converges account state and clears only reservations actually covered by snapshot fence |
| 7 restart | process objects are destroyed/rebuilt; cancel recovery journals fills 5001/5002 to original placement; second recovery is a no-op economically |
| 8 final | independent cash = 97.6, inventory = +6, fills = 6, open ask exposure = 10, no unresolved operations; fresh Risk healthy; kill/recovery gates still block unauthorized fresh placement |

Machine-readable evidence is committed at:

`data/launch_hardening/002/LIFECYCLE_RECONCILIATION.json`

## Minimal implementation corrections exposed by the torture test

### Stable placement/fill identity

`ExecutionJournal.placement_identity_for_exchange_order_id()` resolves an ACKed venue order back to the original placement operation and logical intent. Realtime fills/order updates and cancel recovery use that identity.

A later cancellation can no longer become the owner of a late/authoritative fill merely because it is the newest journal event for the venue order.

### Conservative reservation/snapshot fencing

Each reservation can bind directly to its ACKed venue order ID and ACK wall-clock time. An authoritative account read carries a conservative **read-start** timestamp.

A snapshot only supersedes reservations ACKed at or before that fence. A newer or un-ACKed in-flight placement remains risk-bearing. This is the local #88 overlap required by #110.

### Known-own order recognition

An account `order_update(open=true)` matching a locally ACK-bound reservation is treated as our known order instead of an unknown external order. A truly unknown order still revokes trust and requires reconciliation.

### Fill evidence fails closed

New SUBMISSION evidence stores each intent's action/outcome semantics. The learner joins evidence per logical intent and:

- treats realtime fills as provisional;
- keeps authoritative fills on original placement identity;
- deduplicates stable authoritative fill IDs and deterministic provisional identities;
- makes completeness a **per-leg** property;
- returns unsupported/incomplete rather than supported zero when coverage is missing;
- leaves the still-open ask leg incomplete even when the bid has authoritative fills.

### Account HOLD versus invalid-valuation CANCEL

`GateMode.HOLD` is explicit.

A transient untrusted account state produces no desired quote and the coordinator preserves the current resting quote set without fresh placement/repricing/cancel I/O.

Invalid or stale portfolio marks are different: they force withdrawal of existing resting quotes, including unchanged-price quotes. A stale mark in another held market is sufficient because portfolio valuation itself is invalid. This is the local #78/#108 interaction required by #110.

## Hostile variants

| Variant | Executable evidence | Required outcome |
| --- | --- | --- |
| duplicate ACK/account batch | `test_duplicate_reordered_and_unknown_order_evidence_fail_closed` | applies once / stable identity |
| stale snapshot before in-flight ACK | same | reservation survives snapshot fence |
| known own vs unknown external order | same | known accepted; truly unknown revokes trust |
| fill after cancel evidence | `test_fill_before_order_update_and_cancel_evidence_keeps_original_identity` | original placement/intent retains fill |
| disconnect during placement | `test_uncertain_placement_retains_reservation_and_recovery_authority` | journal/reservation stay UNCERTAIN; recovery-only permit cannot admit fresh exposure |
| invalid/stale multi-market mark | `test_multi_market_invalid_or_stale_mark_and_global_halt_block_fresh_risk` plus MAKE coordinator regressions | fresh Risk blocked; unchanged resting quotes withdrawn |
| transient account resync | `test_transient_account_untrust_holds_resting_quotes_without_new_io` | HOLD: no fresh placement or cancel storm |
| duplicate realtime fill evidence | `test_realtime_only_fill_is_explicitly_provisional` | deterministic dedupe; remains provisional |
| missing fill coverage | `test_missing_fill_coverage_is_not_supported_zero` | unsupported/incomplete, never supported zero |

## Performance / production-path shape

The fixes add bounded direct-index state only:

- dictionary lookup venue-order → logical intent in the reservation book;
- indexed journal lookup on `exchange_order_id/event_type/event_id`;
- no polling;
- no unbounded in-memory scan;
- no background task;
- no execution hot-path join against external state;
- no new event store.

The independent oracle and rich stage snapshots live only in tests/artifacts.

## Remaining limitations — intentional and explicit

1. **Runtime account cash:** `RuntimePortfolio` does not expose cash. The test therefore cross-checks cash against a separate synthetic authoritative account ledger and compares production account/Risk on inventory/exposure. It does not fabricate a runtime cash field.
2. **Realtime fills:** account realtime fill payloads do not carry enough direction/recovery fencing to mutate canonical exposure safely. They remain provisional, revoke trust, and force authoritative reconciliation.
3. **Open-leg evidence:** the final ask is still resting, so learner evidence for the two-sided decision intentionally remains `fill_evidence_incomplete`. The six authoritative bid fills are visible, but “zero ask fills supported” is not claimed.
4. **Fees:** this accepted fill path exposes no fee field. The synthetic venue explicitly declares fees = 0; production fees are not invented.
5. **Legacy journals:** if an old journal lacks a resolvable placement ACK identity, cancel recovery retains its conservative legacy fallback. New journal entries preserve stable placement/intent identity.
6. **This branch is implementation evidence, not grading:** issues are not self-closed and this branch is not self-merged.

## Grading commands

The branch CI runs the repository-wide lint, strict mypy, pytest suite and existing benchmark/smoke gates. The most direct focused reproduction is:

```bash
pytest -q tests/test_launch_hardening_002.py
pytest -q tests/test_live_learn001.py
pytest -q tests/test_build009_account_runtime.py
pytest -q tests/test_make001.py
```

The grader should verify the current branch head rather than trusting this handoff text. Exact final implementation SHA and CI run are recorded on #110/#104 when the branch is marked ready for grading.
