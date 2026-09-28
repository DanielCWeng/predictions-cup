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

## Current strategic posture — 28 September 2026

The repository now contains the completed EXPERIMENT-004C programme on canonical `main`.

- 004C-A: `INCONCLUSIVE / NO PROMOTED EDGE`; generic pairwise internal lead-lag is demoted.
- 004C-B: `SOFT_COMPETITIVE_EFFECT_ONLY`; one narrow Colombia PRE competitive-family effect survives, but no hard/exhaustive-family alpha promotes.
- 004C-C: `INCONCLUSIVE / NO PROMOTED EDGE`; raw PM→SIG 30-second prediction fails mapping-specific falsification and executable crossing economics.
- 004C-D: `NO_CONDITIONAL_EDGE`; frozen age-conditioned and genuine-renewal interactions do not achieve predictive-gain support in the adequately supported challenge cells.

Programme-level conclusion: **no robust executable alpha was established by 004C**. This is a statement about the tested mechanisms, not the complete predictive information set. Broad common-event state, other structural relationships, other external information and other state-dependent models remain open research families.

The only positive 004C discovery worth carrying forward is the narrow 004C-B soft competitive-family structure, and it must not be treated as a general Cup edge without a separately frozen follow-up.

PR #38 / EXPERIMENT-005A remains draft/open and unmerged. Its empirical gate remains outside this 004C close-out. Any broader 005 discovery lane should follow `docs/research/005_DISCOVERY_PRIOR_WORK_BOUNDARY.md`: methodology and prior-work families may be inspected pre-freeze, but detailed prior outcome magnitudes/winners should remain embargoed until the new lane freezes its own feature universe, targets, validation rules, model ladder and screening policy.

The canonical 004C programme handoff is `docs/experiments/EXPERIMENT_004C_FINAL_HANDOFF.md`.

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

The 004C identification programme is complete and frozen. Its four batteries tested internal
propagation, structural family redistribution, mapped cross-venue price discovery and conditional
age/activity/renewal response. No robust executable alpha promoted.

Do not convert those negative results into blanket rejection of price information, structural
models, external information or state-dependent prediction. The next research programme should
broaden the predictor census under the contamination-control convention in
`docs/research/005_DISCOVERY_PRIOR_WORK_BOUNDARY.md`.

EXPERIMENT-005A remains a separate draft scaffold on PR #38 and must consume the final canonical
004C freeze under its own owner/gates before empirical execution.

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

1. Treat `docs/experiments/EXPERIMENT_004C_FINAL_HANDOFF.md` as the canonical programme-level 004C interpretation.
2. Keep PR #38 / EXPERIMENT-005A unmerged until its owner consumes the final 004C freeze SHA and satisfies its own empirical gate.
3. For any 005B–005F discovery work, apply `docs/research/005_DISCOVERY_PRIOR_WORK_BOUNDARY.md` before inspecting detailed historical outcome tables.
4. Continue the independent operational mapping/capture gates; no 004C result changes the prohibition on live execution.
5. Record future negative evidence at the narrow specification level rather than rejecting broad mathematical families by association.

## Open questions

- How many SIG exchanges map EXACTLY, NEAR, DERIVED or not at all to Polymarket?
- Which mappings are stable enough for direct external fair-value use?
- What lead/lag horizon survives fees, queueing and SIG market microstructure?
- Which market families provide useful cross-sectional constraints rather than correlated noise?
- What local retention/compaction/object-storage policy is justified by measured mapping-bounded
  Parquet growth?
- What strategy/risk/execution architecture is warranted after evidence, rather than before it?
