# Maker depth mirror — research-only prototype, NOT FOR DEPLOYMENT

Owner: alpha sprint depth integration, related to #220 and independent safety #207.
Branch: `research/maker-depth-mirror-20261013`; based on `main` at `1f8a65fc`.

## Why

West's LIVE maker already periodically obtains SIG REST full-depth books for its
configured tracked markets. The standalone SIG collector presently tracks zero
full-depth IDs. Starting a second poller without a shared account-wide REST
budget risks starving the maker. This patch introduces **zero additional SIG
REST calls** and records the maker's already-fetched, reconciled books in a
separate JSONL file, if an operator later explicitly enables it.

## Implementation

- `AppSettings.maker_depth_mirror_output_file` defaults to `None`.
  At default there is **no writer thread, no file, no new source callback
  invocation, no extra API request and no change to strategy/risk/execution**.
- When explicitly configured with a new *absolute* `.jsonl` path in an
  isolated research directory, the mirror starts one daemon writer thread.
  The producer only performs `Queue.put_nowait` of **immutable** canonical
  `OrderBook` objects (bounded default 128). Overflow drops records rather
  than ever stalling SIG reconciliation.
- `SigRealtimeStateEngine` calls the optional hook only **after successful
  REST identity verification, current-generation confirmation and trusted-state
  transition**. Failed/stale reconciliations have no accepted output.
- Worker serialises precise full bid/ask ladders and local observation time.
  Each process writes **one 0600 exclusive-create JSONL**; no overwrite, no
  appending active SIG capture, no SQLite/WAL modification. Default file limit
  is 32 MiB; any writer error disables this *research* facility and cannot
  propagate to the maker. On shutdown, logs accepted/written/dropped/errors.
- Captured `venue_timestamp` and `venue_revision` are **null**. SIG
  `OrderBookSnapshotDto` has no native timestamp/revision; canonical
  `OrderBook.timestamp` is locally assigned REST-observed time. The triggering
  realtime revision is distinct from book's source revision. Flags
  `source_clock_verified=false`, `source_sequence_verified=false` and
  `trusted_for_paired_paper=false` deliberately forbid claims of native
  exchange event-time certainty or execution-ready price.
- No queue, disk or IO path is run in signal generation/decision loops. Thread
  startup / shutdown and queue-record callback are inert by default.

## Safety / unresolved blockers

This is a **prototype** and intentionally not deployed. It modifies the
`maker/service.py` construction path and thus requires independent #207
assessment and supervised host acceptance before considering a deployment.

Reviewers must test: startup when setting absent; source callback only after
current trusted generation; no maker state change on disk failure; queue pressure
and large ladders; file-overwrite/symlink protection; CPU p95/p99 event-loop
impact; no sensitive fields; shutdown on cancellation/reconnect; and resource
budget under election-night bursts. No LIVE promotion, deployment, risk-cap
change, process restart or order authorisation follows from this PR.

This mirror does **not** reconstruct missed orderbook deltas, prove source
clock/sequence continuity, prove 5s paired availability, model queue position,
infer fees, release inventory, or generate observed fills/PnL. To move beyond
`NO_TRADE` in #222/#223, Lane B still needs independently reviewed source
attestation, event timing and per-market validity; Lane C needs actual forward
arrival/exit books and empirical order lifecycle reconciliation.

## Local test commands (offline, no network)

```bash
PYTHONPATH=src python -m pytest -q tests/test_sig_maker_depth_mirror.py tests/test_sig_realtime.py
ruff check src/predictions_cup/sig/depth_mirror.py src/predictions_cup/sig/realtime_state.py src/predictions_cup/maker/service.py tests/test_sig_maker_depth_mirror.py tests/test_sig_realtime.py
mypy src/predictions_cup
```

Do **not** add `PREDICTIONS_CUP_MAKER_DEPTH_MIRROR_OUTPUT_FILE` to the live environment or
enable this setting without a separate operator change-control ticket and
independent #207 sign-off. Even then, do an isolated non-LIVE performance
rehearsal before any maker integration, and use a dedicated research path.
