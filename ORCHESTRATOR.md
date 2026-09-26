# Predictions Cup — Orchestrator

This file is the durable project-level orchestration record. It captures intent, dependency order,
review discipline and the reasons behind major decisions. It is not a substitute for the technical
contracts or runbooks.

## Canonical read order

A new orchestrator, reviewer or coding agent should read:

1. `ORCHESTRATOR.md` — intent, priorities, dependencies and review rules;
2. `CURRENT_STATE.md` — what is true on `main` now;
3. `BUILD_LEDGER.md` — accepted build/experiment history;
4. `ARCHITECTURE.md` — implemented technical architecture;
5. `DATA_CONTRACTS.md` — data and semantic contracts;
6. `OPERATIONS.md` — runtime/operator procedure;
7. the relevant ticket, experiment or research document.

GitHub merge state and the actual contents of `main` outrank stale prose. Chat history is working
context, not canonical project state.

## Mission

Build a robust prediction-market trading/research system for the 2026 SIG Predictions Trading Cup,
using SIG market state plus external prediction-market information to test simple, falsifiable
sources of edge before introducing execution complexity.

No empirical edge is assumed. The current project must earn each strategy step through replay,
historical evidence, live paired capture and paper/shadow validation.

## Hard constraints

- Trading capability remains **NONE** until separately approved execution work exists.
- SIG REST is authoritative for SIG financial/state truth; realtime accelerates or invalidates state
  but does not replace authoritative reconciliation.
- Broad tournament Realtime does not imply broad resident full depth.
- Production Polymarket capture must be bounded by the accepted SIG ↔ Polymarket crosswalk.
- High-frequency Polymarket research history uses Parquet + ZSTD; SQLite is operational metadata /
  health only.
- Credentials never belong in GitHub, logs, prompts or screenshots.
- Builders do not self-accept. Code review and live acceptance are separate gates when runtime
  behavior matters.
- Strategy claims require actual data; synthetic fixtures prove mechanics, not edge.

## Current strategic posture — 26 September 2026

The read-only infrastructure foundation is substantially in place:

- BUILD-001 through BUILD-007 are merged/accepted;
- EXPERIMENT-001A and EXPERIMENT-002 are merged/accepted;
- MAPPING-001 is an accepted framework, but the live 2026 crosswalk is still outstanding;
- BUILD-006 live validation established the 237-exchange SIG tournament shape and a governed
  tracked-depth runtime;
- BUILD-007 live validation established systemd supervision plus a strict-universe,
  ARM64-compatible Parquet research lane;
- EXPERIMENT-002 makes lead/lag, response-curve, relative-value and LOO experiments executable, but
  no live or historical result is yet accepted as evidence of edge.

The initial empirical strategy lane stays deliberately simple:

1. direct external-market lead/lag;
2. response/half-life measurement;
3. simple residual / relative-value relationships;
4. LOO-PRICE / LOO-FAMILY structure where mappings support it;
5. selective market making only after state, mapping and replay evidence justify it.

## Dependency graph

```text
accepted SIG runtime (BUILD-006)
            +
accepted supervised runtime/storage (BUILD-007)
            |
            v
LIVE-MAPPING-GATE-001
accepted SIG <-> Polymarket crosswalk
            |
            v
mapping-bounded paired live capture
            |
            +--> deterministic replay
            |        |
            |        v
            |   EXPERIMENT-002 on real data
            |        |
            |        v
            |   lead/lag / RV / LOO evidence
            |
            +--> production soak / runtime validation
                     |
                     v
             paper/shadow strategy work
                     |
                     v
          risk + execution only if justified
```

Historical research runs in parallel where it can answer the same hypotheses without weakening
live-data priorities.

## Active workstreams

### 1. LIVE-MAPPING-GATE-001 — primary gating work

Generate and independently accept the live SIG ↔ Polymarket crosswalk for the tournament universe.
The mapping framework already exists; production IDs do not.

Acceptance requires live SIG enumeration, reviewer-owned promotion/overrides, exact coverage
evidence, mapped-token CLOB availability and a deterministic acceptance artifact.

The observed SIG universe is roughly 237 exchanges. The exact number of Polymarket markets/tokens
will be determined by the accepted crosswalk; it must not be guessed in runtime configuration.

### 2. Paired live capture

After mapping acceptance, supervise only mapped Polymarket counterparts. Broad Gamma metadata
discovery may still support mapping/metadata maintenance, but broad 1 Hz WebSocket/depth capture is
not the production lane.

Then run a mapping-bounded soak and complete the remaining production runtime checks, including
SSH independence and reboot recovery.

### 3. Replay + empirical experiments

EXPERIMENT-002 is accepted machinery, not accepted alpha. Run the lead/lag, response, relative-value
and LOO suites on accepted historical/live data and record negative results as seriously as positive
ones.

The Monday 28 September gate remains useful: if the initial hypotheses are not runnable through
our own data/replay path, priority collapses back onto data -> replay -> experiment rather than
adding strategy complexity.

### 4. Historical data lane

Historical order-book/fill research remains useful for regime behavior and pre-competition
falsification. HIST-DATA-001 / PR #17 was closed unmerged and should not be treated as accepted
capability. A replacement historical lane should reuse accepted replay/data contracts rather than
becoming a parallel architecture.

### 5. Monitoring UI

A terminal-style operator/research UI is useful for visibility and competition operations, but it is
non-gating. It should surface existing state, capture health, mapping, lag/edge diagnostics and later
paper/live strategy state; it must not drive architecture ahead of the research path.

## Accepted decisions

### Mapping-bounded Polymarket capture

The broad election heuristic selected 3,160 markets / 6,320 tokens in live testing. That was useful
as a stress test but is not the competition production scope. The supervised service therefore
requires explicit external IDs from the accepted mapping and fails closed on missing/unresolved IDs.

See `docs/decisions/001-mapping-bounded-polymarket-capture.md`.

### Parquet for high-frequency Polymarket research history

The broad SQLite/WAL soak produced an operationally unacceptable storage shape on the ~30 GiB EC2
host. The 1-second research cadence was retained, but the physical format changed to immutable
ZSTD Parquet shards. SQLite remains for low-volume metadata/health.

See `docs/decisions/002-polymarket-parquet-research-storage.md`.

### SIG broad state vs tracked depth

All tournament exchanges may be known and observed through Realtime/bulk scalar state while only an
explicit subset has authoritative resident full depth. Untracked depth is not silently treated as
trusted. BUILD-006 is the accepted contract.

## Review / acceptance protocol

For implementation work:

1. define the ticket and explicit non-goals;
2. coding agent implements on a branch;
3. independent review checks the actual diff/tests, not only the handoff;
4. **CODE GREEN** means repository correctness is accepted at an exact SHA;
5. if runtime behavior matters, run the smallest meaningful live gate on that exact SHA;
6. **LIVE GREEN** means the observed operational evidence satisfies the preregistered gate;
7. only then merge;
8. reconcile canonical state after merge.

A passing CI run is necessary but not sufficient for venue/runtime-sensitive work.

## Immediate next actions

1. Ensure the temporary three-market / six-token BUILD-007 smoke universe is not mistaken for
   production configuration; Polymarket may remain disabled until mapping is accepted.
2. Execute LIVE-MAPPING-GATE-001 / issue #13.
3. Populate the supervised Polymarket universe from the accepted crosswalk.
4. Run the mapping-bounded paired capture soak and remaining production SSH/reboot checks.
5. Run EXPERIMENT-002 against accepted real data.
6. Use the results to decide whether lead/lag, relative value, LOO structure or selective market
   making deserves the next strategy build.

## Open questions

- How many SIG exchanges map EXACTLY, NEAR, DERIVED or not at all to Polymarket?
- Which mappings are stable enough for direct external fair-value use?
- What lead/lag horizon survives fees, queueing and SIG market microstructure?
- Which market families provide useful cross-sectional constraints rather than correlated noise?
- What local retention/compaction/object-storage policy is justified by measured mapping-bounded
  Parquet growth?
- What strategy/risk/execution architecture is warranted after evidence, rather than before it?
