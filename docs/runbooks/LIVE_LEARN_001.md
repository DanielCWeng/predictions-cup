# LIVE-LEARN-001 runbook

## Safety boundary

LIVE-LEARN is observation/scoring only. It is wired behind SHADOW-002
persistence and has no order-placement capability.

**REAL SIG ORDERS SENT BY THIS LANE: NO**

## Enable

LIVE-LEARN requires the existing MAKE + SHADOW runtime:

```text
PREDICTIONS_CUP_MAKER_ENABLED=true
PREDICTIONS_CUP_SHADOW_ENABLED=true
PREDICTIONS_CUP_LIVE_LEARN_ENABLED=true
```

Recommended launch paths:

```text
PREDICTIONS_CUP_SHADOW_JOURNAL_PATH=data/shadow_002/events.jsonl
PREDICTIONS_CUP_LIVE_LEARN_OUTCOME_PATH=data/live_learn/outcomes.jsonl
PREDICTIONS_CUP_LIVE_LEARN_REPORT_PATH=data/live_learn/reports
PREDICTIONS_CUP_LIVE_LEARN_QUEUE_CAPACITY=200000
PREDICTIONS_CUP_LIVE_LEARN_EVIDENCE_GRACE_SECONDS=5
PREDICTIONS_CUP_LIVE_LEARN_MAX_EVIDENCE_AGE_SECONDS=15
```

No new systemd service is required. Restart the existing MAKE service after
changing environment configuration. SHADOW constructs the LIVE-LEARN mirror
during normal service startup.

## Outputs

Authoritative inputs remain:

```text
data/shadow_002/events.jsonl
data/execution_journal.sqlite3
```

LIVE-LEARN writes:

```text
data/live_learn/outcomes.jsonl
data/live_learn/reports/5m/<UTC>.json
data/live_learn/reports/5m/<UTC>.md
data/live_learn/reports/15m/<UTC>.json
data/live_learn/reports/15m/<UTC>.md
data/live_learn/reports/1h/<UTC>.json
data/live_learn/reports/1h/<UTC>.md
```

The outcome journal is append-only. Report filenames are cadence-boundary UTC
timestamps.

## Startup/restart behaviour

Startup replays the SHADOW journal before accepting new scoring events. Existing
outcome IDs are deduplicated. Pending horizons are reconstructed from decisions
that have no outcome record.

A restart can therefore occur with pending 1s/5s/15s/30s/60s/5m maturities
without losing them. UTC observation time is used across restart; previous-boot
monotonic values are not used as wall-clock deadlines.

If a future observation was already captured before the restart, replay can
score it immediately. If the evidence never arrived within the grace window, an
explicit missing/stale outcome is written.

## What to watch

The LIVE-LEARN mirror contributes to composite SHADOW persistence health. Check:

- healthy=true;
- failures=0;
- queue depth returning toward zero;
- queue high-water below configured capacity;
- outcomes file continuing to grow;
- 5m reports appearing on UTC cadence.

A full queue is not a drop condition. It is an explicit unhealthy condition.
The durable SHADOW JSONL is the recovery source.

## Interpreting statuses

`MATURED_SCORED`: trusted price evidence existed at/after the exact horizon.

`SOURCE_UNAVAILABLE`: no usable future price arrived in the allowed window.

`STALE_UNTRUSTED_EVIDENCE`: observed evidence was stale, untrusted or arrived
after the configured grace window.

`EXECUTION_EVIDENCE_UNAVAILABLE`: price scoring may be valid, but there is no
accepted fill/execution chain for quote economics.

`UNSUPPORTED_SCORE_SEMANTICS`: the candidate emitted an output whose meaning
has no registered scorer. Do not reinterpret `score` manually.

`DECISION_ABSTAINED`: the candidate explicitly abstained.

`INSUFFICIENT_HISTORY`: the input snapshot or another required historical
piece was unavailable.

## Fill evidence rules

Accepted fill evidence is explicit BUILD-009 evidence such as
`FILL_SUMMARY` or `AUTHORITATIVE_FILL`. Do not treat a market trade-through
as a fill.

SIG's `GET /orders/{id}/fills` contract is the authoritative order-fill
backfill surface and is already implemented in `SigRestClient`. Do not add a
per-decision polling loop. If later fill coverage needs expansion, implement a
batched `ExecutionEvidenceProvider` and keep it outside the scoring math.

## Report interpretation

Outcome horizon and report cadence are separate. A 5-minute report can contain
1s, 5s, 15s, 30s, 60s and 5m outcomes where those horizons have matured.

Matched-support deltas compare only identical snapshot+horizon observations.
They are descriptive evidence, not an automatic champion-selection rule.

`thin_evidence=true` currently means fewer than 10 scored outcomes for that
candidate in the report cohort. Treat it as a warning, not a performance claim.

## Tests and benchmark

Run:

```bash
pytest tests/test_live_learn001.py
python scripts/benchmark_live_learn001.py --markets 237 --candidates 6
```

CI also runs the 237-market burst after the existing SHADOW benchmark/soak.

The benchmark validates bounded-queue processing, deterministic one-second
maturity and outcome cardinality. It does not place orders or call SIG.

## Extending

To add 15-minute or 1-hour outcome horizons, pass `900` and `3600` in the
engine horizon sequence. No scheduler changes are required.

To add a candidate score, implement `OutcomeScorer` and register it. Candidate
specific semantics belong there, not in reporting or persistence.

To replace persistence, implement the same outcome/report sink boundaries. Do
not add a second candidate bus.

## Operator stop conditions

Investigate before trusting reports if:

- composite SHADOW persistence becomes unhealthy;
- LIVE-LEARN queue reaches capacity;
- outcome journal stops advancing while decisions continue;
- stale/untrusted evidence rate jumps;
- matched support collapses;
- execution evidence unexpectedly disappears for a previously covered strategy.

Do not automatically switch LIVE strategy based on these short-window reports.
