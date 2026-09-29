# Build Ledger

Status semantics:

- `MERGED / ACCEPTED` — accepted functionality/evidence exists on `main`.
- `MERGED / ACCEPTED RESEARCH` — accepted empirical record exists on `main`; this does not imply strategy promotion.
- `BLOCKED / IN REVIEW` — implementation/evidence exists on a branch/PR but has a known blocker and is not canonical.
- `PLANNED` — no accepted implementation exists.
- An unmerged branch must never be described as implemented on `main`.

| Ticket | Status | Branch | PR | Head / merge | Canonical disposition |
|---|---|---|---:|---|---|
| BUILD-001 — Repository foundation | MERGED / ACCEPTED | `build/001-foundation` | #1 | head `e0695cfe...`; merge `88a2578e...` | Project control, CI, package boundaries. |
| BUILD-002 — Config, secrets, canonical models | MERGED / ACCEPTED | `build/002-config-domain-models` | #2 | head `7d2dfc21...`; merge `81cee3c0...` | Typed settings/domain contracts; no execution. |
| BUILD-003 — SIG authenticated read-only REST | MERGED / ACCEPTED | `build/003-sig-rest-client` | #4 | head `c788125d...`; merge `9e4aef9d...` | Read-only SIG transport. |
| EXPERIMENT-001A — Polymarket capture foundation | MERGED / ACCEPTED | `experiment/001a-polymarket-live-capture` | #5 | head `86b4a0ab...`; merge `f1783c0e...` | Public read-only research capture. |
| BUILD-004 — SIG Realtime state/reconciliation | MERGED / ACCEPTED | `build/004-sig-realtime-state` | #12 | head `21ee5776...`; merge `77d44a5d...` | Replayable full-tournament event/state capture. |
| MAPPING-001 — SIG ↔ Polymarket identity layer | MERGED / ACCEPTED LIVE CROSSWALK | `mapping/001-sig-polymarket-crosswalk` + accepted live artifacts | #9 | framework merge `8aba24e9...`; accepted artifacts on `main` | 237 SIG exchanges: 140 EXACT, 87 DERIVED, 4 NEAR, 6 NO_TRADE; 693 CIDs / 1,386 tokens; all records verified. |
| BUILD-005 — Deterministic replay / experiment foundation | MERGED / ACCEPTED | `build/005-deterministic-replay` | #15 | merge `2e732414...` | Observable-time replay, executable markouts and experiment contract. |
| EXPERIMENT-002 — Lead/lag, RV, LOO experiments | MERGED / ACCEPTED | `experiment/002-leadlag-rv-loo` | #16 | head `d9e6df63...`; merge `ab530e7d...` | Machinery accepted; no automatic alpha claim. |
| BUILD-006 — SIG REST governor / tracked depth | MERGED / ACCEPTED | `build/006-sig-rest-governor` | #19 | head `168bf4e5...`; merge `7f3ba788...` | Broad scalar state + explicit tracked full-depth trust. |
| BUILD-007 — EC2 supervision / always-on capture | MERGED / ACCEPTED | `build/007-ec2-runtime-supervision` | #20 | head `1fc3383a...`; merge `153116bb...` | ARM64/Parquet runtime gate passed; mapping-bounded production soak + SSH/reboot validation still outstanding. |
| BUILD-008 — Research evaluation harness | MERGED / ACCEPTED | `build/008-research-evaluation-harness` | #24 | head `9419a3bf...`; merge `c56c225a...` | Canonical spec/run identity, as-of, split, FDR, bootstrap, stability, ablation and stress machinery. |
| BUILD-009 — Low-latency strategy & execution core | BLOCKED / IN REVIEW | `build/009-low-latency-strategy-execution-core` | #50 | benchmarked correction code `7ea841e...`; CI #2467 PASS | Independent-review correction pass implemented: Risk-bound mode + LIVE permit, synchronous reservation overlay, worst-case gross accounting, fail-closed Realtime fills, malformed accepted responses -> UNCERTAIN, depth-aware SHADOW, reserved HIGH-priority REST capacity, longer keepalive and exact wire bytes. 3k + explicit 100k + repeated 1m target-host evidence complete; 547 passed / 2 skipped. Remains draft/non-canonical pending correction re-review; no real orders sent. |
| DATA-001 — Historical replay corpus | MERGED / ACCEPTED | `data/001-historical-replay-corpus` | #23 | head `4ae36174...`; merge `18b5a4dc...` | Historical corpus/provenance accepted. |
| DATA-002 — Fee/refund/rebate evidence | MERGED / ACCEPTED | `data/002-polymarket-fees` | #36 | head `aebefdef...`; merge `69cb1924...` | Fill-complete raw attribution evidence; no universal derived taker truth label. |
| EXPERIMENT-004C-A | MERGED / ACCEPTED RESEARCH | `experiment/004c-a-information-freshness` | #34 | merge `117a584c...` | INCONCLUSIVE / NO PROMOTED EDGE. |
| EXPERIMENT-004C-B | MERGED / ACCEPTED RESEARCH | `experiment/004c-b-structural-redistribution` | #35 | merge `777354f9...` | SOFT_COMPETITIVE_EFFECT_ONLY. |
| EXPERIMENT-004C-C | MERGED / ACCEPTED RESEARCH | `experiment/004c-c-crossvenue-price-discovery` | #37 | merge `751118f3...` | INCONCLUSIVE / NO PROMOTED EDGE. |
| EXPERIMENT-004C-D | MERGED / ACCEPTED RESEARCH | `experiment/004c-d-conditional-response-renewal` | #39 | merge `6aa6a6c9...` | NO_CONDITIONAL_EDGE. |
| EXPERIMENT-005A — Fee-role structure discovery | MERGED / ACCEPTED RESEARCH | `experiment/005a-fee-role-structure-discovery` | #38 | head `b323aa53...`; merge `4a7041ce94d109f17e98267ff358cf539b62417f` | Narrow same-family PRE 5s effect only; no broad role-aware edge. |
| EXPERIMENT-005B — Historical predictive atlas | BLOCKED / IN REVIEW | `experiment/005b-historical-predictive-atlas` / `experiment/005b-ordering-falsification` | #45 | parent head `232c7c39...`; remediation head `8c99a5e6...` | Frozen block-aware falsification implementation exists; no corrected empirical results yet. Original HOLDOUT findings remain non-canonical. |
| EXPERIMENT-005C — Joint panel prediction | MERGED / ACCEPTED RESEARCH | `experiment/005c-joint-panel-prediction` | #41 | head `cd84b3de...`; merge `c5f8718828dcd62b7d3e35e73fd1430c8a4af6f5` | DOWNGRADE — familywise null not rejected; shadow comparator only. |
| EXPERIMENT-005D — Structural/RV atlas | MERGED / ACCEPTED RESEARCH | `experiment/005d-structural-rv-atlas` | #42 | head `4ddc27e8...`; merge `0de054c5ab9981f2c57fcd9506df0220dab50203` | Narrow LATE_COUNT structural-convergence evidence only. |
| EXPERIMENT-005E — Participant ecology | MERGED / ACCEPTED RESEARCH | `experiment/005e-participant-ecology-atlas` | #43 | head `fc2bf6f4...`; merge `39fe922b3aacda4a8f202d1e905a952c07268027` | NO_INCREMENTAL_EVIDENCE on primary target. |
| EXPERIMENT-005F — Microstructure/liquidity atlas | MERGED / ACCEPTED RESEARCH | `experiment/005f-microstructure-liquidity-atlas` | #44 | head `060b58b0...`; merge `4efa3dd002abc1cbf1997acdf8734adee2e13f4c` | Qualified state-hazard support; strongest result is persistent economic-BBO age / renewal hazard. |
| INFRA — GitHub Actions → Kaggle runner | MERGED / ACCEPTED | `infra/kaggle-actions-runner` | #47 | head `3781d40f...`; merge `793ff521c64ad15e12377d3c75d1053ad76582a7` | Canonical routine Kaggle execution path; max five parallel jobs. |
| DATA-003 — SIG actual-market fills | MERGED / ACCEPTED | `data/003-sig-actual-fills` | #48 | head `a9ef91fa...`; merge `3e4f9ac350ba6a33dfbc9a300a00a933efdfce42` | 132,928 mapped-universe fills; fill-complete evidence; explicit custody gaps; zero DATA-001/002 overlap. |
| DATA-004 — ETS P0/P1 market-graph fills | BLOCKED / IN REVIEW | `data/data004-ets-p0p1-fills` | pending | pending | 231,964 fills across 298 markets; 4,989 rows lack custody blocks on Sep 20–21 and maker/taker symmetry is unresolved. OCI source is immutable; private Kaggle package is pending manual upload. Branch is not canonical. |

## Closed / superseded branch-only work

The following categories may remain as GitHub refs for provenance but are not missing canonical
capability and should not be merged into `main` merely because they are technically ahead/diverged:

- pre-final 005A launch/audit/packaging branches;
- independent pre-004C Sol/Astra/Opus hypothesis-generation branches;
- the old HIST-DATA-001 audit/adapter branch, superseded by accepted DATA-001 + BUILD-005;
- the old UI research branch based on pre-mapping state;
- backup and temporary branches.

The only active unmerged scientific lane after this reconciliation is EXPERIMENT-005B / PR #45.

GitHub merge state and actual `main` contents are authoritative when documentation drifts.
