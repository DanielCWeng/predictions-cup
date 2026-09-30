# LIVE-LEARN-001 — Automatic Decision Scoring

## Status

Implementation branch: `build/live-learn-001`

Base main SHA: `0b91e5c4e8ed461e7e5e8fcc880a094029f58891`

LIVE-LEARN extends SHADOW-002. It does not create a second candidate bus and it has no order-write capability.

## Architecture

The production flow is:

```text
MakerRuntimeLoop
  -> CanonicalShadowSnapshot
  -> SHADOW-002 candidate fan-out
  -> CandidateDecision
  -> primary SHADOW JSONL + CAPTURE mirror
  -> LIVE-LEARN mirror
       -> bounded input queue
       -> per-exchange maturity heaps
       -> one global expiry/report deadline loop
       -> future CanonicalShadowSnapshot evidence
       -> registered scorer
       -> append-only DecisionOutcome JSONL
       -> 5m / 15m / 1h JSON + Markdown reports
```

The existing SHADOW JSONL remains the authoritative replay stream. LIVE-LEARN is a
`ShadowEventStore`-compatible mirror behind `CompositeShadowEventStore`.

There is no task per market and no sleeping task per decision. One worker owns
scoring state. Per-exchange heaps mature decisions only when a relevant future
snapshot arrives. One deadline task handles evidence-expiry and report deadlines.

## Time semantics

Decision time is `CandidateDecision.observed_at` in UTC. Outcome maturity is:

```text
maturity_at = decision.observed_at + outcome_horizon
```

Wall-clock UTC is the durable cross-restart time domain. Monotonic timestamps are
used only for same-process source-freshness and BUILD-009 execution attribution.

A future price is eligible only when:

```text
future.observed_at >= maturity_at
```

An observation exactly on the boundary is eligible. An observation before the
boundary is never used. Evidence may arrive inside the configured grace window;
after the grace window it is recorded as late/stale rather than silently shifted
to a different horizon.

Default horizons are 1s, 5s, 15s, 30s, 60s and 300s. The engine accepts an
arbitrary positive horizon sequence, so 15-minute and 1-hour horizons require no
core scheduler changes.

Report cadence is independent of outcome horizon. Default report cadences are
300s, 900s and 3600s.

## Durable outcome contract

`DecisionOutcome` is versioned and has a deterministic ID derived from:

```text
decision_id + scoring_spec_id + scoring_spec_version + horizon_seconds
```

It records candidate/version/family, input snapshot, maturity time, evidence
source IDs, price source/timestamp/freshness/trust, price convention, component
statuses, typed numeric metrics, dimensions and missing reason.

The outcome journal is append-only JSONL. Existing IDs are loaded on startup and
are never appended twice.

Failure/status vocabulary includes:

- `MATURED_SCORED`
- `HORIZON_NOT_MATURED` for scheduler/report state
- `SOURCE_UNAVAILABLE`
- `STALE_UNTRUSTED_EVIDENCE`
- `EXECUTION_EVIDENCE_UNAVAILABLE`
- `UNSUPPORTED_SCORE_SEMANTICS`
- `DECISION_ABSTAINED`
- `INSUFFICIENT_HISTORY`

A scored price outcome can still carry an unsupported forecast component or
unavailable execution component. Those states are not collapsed.

## Market evidence

LIVE-LEARN consumes the same canonical SIG book state already frozen into
`CanonicalShadowSnapshot`. It does not poll a second market-data stack.

For each evidence point it records:

- initial/future YES-probability midpoint;
- best bid/ask where present;
- canonical snapshot ID;
- source ID;
- source timestamp;
- source freshness;
- source trust;
- YES-probability orientation.

Generic midpoint markout is always computed when both trusted midpoints exist.
Executable markout is only computed for an explicit BUY/SELL action with the
required executable sides. It is not manufactured for ambiguous quote intent.

## Execution/fill evidence

`ExecutionEvidenceProvider` is replaceable. The production default is
`JournalExecutionEvidenceProvider`, which reads BUILD-009's existing
SQLite/WAL execution journal.

Correlation is:

```text
decision_id
 -> candidate_id + exchange_id + decision monotonic boundary
 -> BUILD-009 SUBMISSION
 -> logical operation
 -> explicit FILL_SUMMARY / AUTHORITATIVE_FILL evidence
 -> DecisionOutcome
```

Passive trade-through is never treated as a fill.

The supplied SIG API contract also exposes `GET /orders/{id}/fills` with
per-fill IDs/timestamps and exact lifecycle `totalQuantityFilled` /
`avgFillPrice`. The existing `SigRestClient.get_order_fills()` implements
that contract. LIVE-LEARN deliberately does not poll it per decision/horizon,
because doing so would turn scoring into a rate-limited task-per-order path.
Authoritative fills already journalled by BUILD-009 are consumed; a future
execution-evidence provider can batch/backfill the same endpoint without
changing scoring mathematics.

If a mixed-side execution cannot be joined unambiguously to per-intent fills,
the provider returns `mixed_side_execution_requires_intent_fill_join` instead
of assigning a side by guess.

## Scorer registry

The generic runtime never interprets `CandidateDecision.score` as a
probability.

`ScorerRegistry` selects a registered plugin from the decision schema:

- `QuoteEconomicsScorer` for explicit quote intent;
- `DirectPmResidualScorer` for the direct-PM reference's documented direction
  semantics;
- `FairValueScorer` for an explicit `fair_value`;
- no match -> `UNSUPPORTED_SCORE_SEMANTICS`.

A new scoring method is added by implementing `OutcomeScorer` and registering
it. No SHADOW bus changes are required.

Current metrics, when supported, include midpoint markout, executable markout,
forecast error, signed FV error, direction accuracy, signal decay, fill rate,
partial-fill rate, average fill price, spread capture, realised spread,
post-fill markout, adverse selection, fill latency and candidate compute latency.

No Brier score is emitted until a scorer has an actual binary resolution target.
005F hazard output is not reinterpreted as probability or direction.

## Reports and matched support

Reports are emitted as JSON and Markdown at 5m, 15m and 1h cadence. They include:

- decision/firing/abstention counts and rates;
- matured/scored outcome counts;
- outcome coverage and stale/untrusted evidence rate;
- mean supported metrics;
- evidence concentration by market;
- breakdowns by market, mapping class, liquidity bucket, regime and family where
  those dimensions exist;
- matched-support comparisons against direct PM and MAKE baselines.

Matched comparisons require identical `input_snapshot_id + horizon` support.
The report never promotes or switches a candidate automatically. Thin evidence
is labelled rather than extrapolated.

## Restart and idempotency

On startup LIVE-LEARN:

1. loads existing outcome IDs;
2. replays the authoritative SHADOW JSONL in event order;
3. restores decisions and initial snapshots;
4. rebuilds pending maturity heaps;
5. scores any replayed future evidence not already persisted;
6. expires past-due items with explicit missing/stale status;
7. resumes one worker and one deadline loop.

This survives process restart and host reboot because maturity uses UTC
`observed_at`, not a previous boot's monotonic clock.

## Backpressure and failure isolation

LIVE-LEARN input is bounded. Queue overflow raises an explicit error and marks
health unhealthy; it never silently discards a decision. The primary SHADOW
journal remains the replay source, so an operator can recover scoring from
durable evidence after fixing the downstream failure.

Filesystem work is off the MAKE hot path. Outcome writes are batched per matured
market snapshot and fsync append-only JSONL. Reports use atomic replace into
timestamped files.

## Configuration

- `PREDICTIONS_CUP_LIVE_LEARN_ENABLED`
- `PREDICTIONS_CUP_LIVE_LEARN_OUTCOME_PATH`
- `PREDICTIONS_CUP_LIVE_LEARN_REPORT_PATH`
- `PREDICTIONS_CUP_LIVE_LEARN_QUEUE_CAPACITY`
- `PREDICTIONS_CUP_LIVE_LEARN_EVIDENCE_GRACE_SECONDS`
- `PREDICTIONS_CUP_LIVE_LEARN_MAX_EVIDENCE_AGE_SECONDS`

Enabling LIVE-LEARN requires SHADOW enabled. It does not require LIVE trading.

## Known scoring gaps

- no inferred passive SHADOW fills;
- late fills not yet journalled by BUILD-009 cannot be scored until explicit
  execution evidence is available;
- mixed-side execution without an intent/fill join fails closed;
- 005F hazard calibration needs a separately registered, frozen event target;
- binary Brier scoring requires resolution evidence, not an interim market price;
- liquidity/regime breakdowns appear only when candidate payloads provide those
  accepted dimensions.

These gaps are explicit in outcome/report status rather than approximated.

## Safety

LIVE-LEARN has no SIG write client and does not alter BUILD-009 risk, MAKE quote
generation, PRED-006 or EXPERIMENT-005F.

**REAL SIG ORDERS SENT: NO**
