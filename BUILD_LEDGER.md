# Build Ledger

Status semantics:

- `MERGED / ACCEPTED` — accepted functionality exists on `main`.
- `MERGED / ACCEPTED FRAMEWORK` — accepted framework exists on `main`, but a separately tracked
  live acceptance gate is still outstanding.
- `IN REVIEW` — requested corrections are implemented on the named branch/PR and await independent
  acceptance; the work is not part of `main`.
- `BLOCKED / IN REVIEW` — implementation exists only on the named branch/PR and still has a known
  blocker.
- `PLANNED` — no accepted implementation exists.
- An unmerged branch must never be described as implemented on `main`.

| Ticket | Status | Branch | PR | Commit | Review | Acceptance |
|---|---|---|---|---|---|---|
| BUILD-001 — Repository Foundation and Canonical Project Control | MERGED / ACCEPTED | `build/001-foundation` | #1 | head `e0695cfe61a3b5070d08e64fe872da676b32db75`; merge `88a2578e25f4ee005a01955db7d9ef619ce5be30` | final review complete; CI green | merged 24 Sep 2026 |
| BUILD-002 — Configuration, secrets and typed API/domain models | MERGED / ACCEPTED | `build/002-config-domain-models` | #2 | head `7d2dfc218e1ddc46685346b8a683bdc8a041c9ce`; merge `81cee3c0449fb5abe898df46c752ba312a5c8422` | blocker revision accepted; CI green | merged 24 Sep 2026 |
| BUILD-003 — SIG authenticated read-only REST client | MERGED / ACCEPTED | `build/003-sig-rest-client` | #4 | head `c788125df685950bf389df1adea56d166b3f9d23`; merge `9e4aef9d5bb485ca72d90a6b164e79ef06209757` | blocker revision accepted; final CI green | merged 25 Sep 2026 |
| EXPERIMENT-001A — Polymarket live data capture foundation | MERGED / ACCEPTED | `experiment/001a-polymarket-live-capture` | #5 | head `86b4a0ab6f64f00c81a1c265db3a90bff878bf00`; merge `f1783c0e5f6088f4e690ef02bc0df967e546ad57` | recorder revision accepted; final CI green | merged 25 Sep 2026 |
| BUILD-004 — SIG realtime state, REST reconciliation and replayable capture | MERGED / ACCEPTED | `build/004-sig-realtime-state` | #12 | head `21ee5776610aa139e14f0fbc681030465e2e8c51`; merge `77d44a5d540075ece3ae77ffa679afb81aa8307b` | expiry-safety refresh accepted; original live smoke exposed the all-exchange depth scaling defect later corrected by BUILD-006 | merged 25 Sep 2026 |
| MAPPING-001 — Canonical SIG ↔ Polymarket identity crosswalk | MERGED / ACCEPTED FRAMEWORK | `mapping/001-sig-polymarket-crosswalk` | #9 | head `6ffdcd4a00f435a1be651142440fc7d18f17dd61`; merge `8aba24e967ba981acabbf50d60cb52d8abc1463a` | mapping framework accepted; live 2026 crosswalk not yet generated/accepted; tracked by LIVE-MAPPING-GATE-001 / issue #13 | merged 25 Sep 2026; framework only |
| BUILD-005 — Deterministic replay & experiment foundation | MERGED / ACCEPTED | `build/005-deterministic-replay` | #15 | merge `2e73241400cd8bcf5cb0df3f06652f9bad9a0b96` | observable-time replay, accepted-schema loaders, executable markouts, experiment/evaluation contract and synthetic acceptance fixture accepted | merged 25 Sep 2026 |
| EXPERIMENT-002 — Lead/lag, relative value and LOO-family experiments | MERGED / ACCEPTED | `experiment/002-leadlag-rv-loo` | #16 | head `d9e6df63bdc4621a1c2155dc246935154c7bbd8d`; merge `ab530e7d8e46d45075d958baae25f19488731f92` | experiment machinery accepted; no empirical edge claimed until run on accepted real data | merged 25 Sep 2026 |
| BUILD-006 — SIG live REST governor and tracked-depth universe | MERGED / ACCEPTED | `build/006-sig-rest-governor` | #19 | head `168bf4e5d002d6df9707d0424eae3bba280402f1`; merge `7f3ba788739083e2653cd5e712628e0e63e96201` | tracked-depth/governor revisions accepted; final CI green; accepted 60-second credentialed smoke: 237 known, 1 tracked / 236 untracked, 0 429s, 0 reconciliation failures, 2 full-book reads, tracked book inside 30s freshness bound | merged 26 Sep 2026 |
| BUILD-007 — EC2 runtime supervision / always-on capture | MERGED / ACCEPTED | `build/007-ec2-runtime-supervision` | #20 | head `1fc3383ac2471466ef440b5f050559ba0a37deed`; merge `153116bb84bc64f202b4cd6dc7748e11d1a84e8b` | code review green; CI #685 green; ARM64 EC2 Parquet live gate passed on 3-market / 6-token explicit test universe; scheduled Gamma refresh + positive 1s 429 floor + restart/readback all validated; production mapping-bounded soak remains downstream of LIVE-MAPPING-GATE-001 | merged 26 Sep 2026 |

| DATA-001 — Historical replay corpus | MERGED / ACCEPTED | `data/001-historical-replay-corpus` | #23 | head `4ae36174ca6abc63f4a5fc8d623a1e84bdf3993e`; merge `18b5a4dcbaea5c6cb4ae040a38bc3769d1eb3c9a` | independent review PASS_WITH_FOLLOWUP completed; accepted corpus/provenance used by subsequent experiments | merged 26 Sep 2026 |
| DATA-002 — Polymarket fee/maker-taker evidence dataset | MERGED / ACCEPTED | `data/002-polymarket-fees` | #36 | head `aebefdeffc05240b049c6f5e624596c5f60eaa8e`; merge `69cb1924751515a495bf99556819147ad090d67d` | raw fee/refund/rebate attribution evidence accepted; no derived maker/taker truth label implied | merged 28 Sep 2026 |
| EXPERIMENT-004C-A — Information Propagation / Freshness | MERGED / ACCEPTED | `experiment/004c-a-information-freshness` | #34 | head `2782277a46ad3b2147a6e85788c85f2ce77405fd`; merge `117a584c990c2a5488094200c1522de4261b66be` | `INCONCLUSIVE / NO PROMOTED EDGE`; CI green; evidence/provenance preserved | merged 28 Sep 2026 |
| EXPERIMENT-004C-B — Structural / Competitive-Family Redistribution | MERGED / ACCEPTED | `experiment/004c-b-structural-redistribution` | #35 | head `65bd8a81a7e4ef36e920df5e092a37b51cc00eb7`; merge `777354f9aa6b56012b193853a2ae544e08be5ce3` | `SOFT_COMPETITIVE_EFFECT_ONLY`; no hard family promoted; CI green | merged 28 Sep 2026 |
| EXPERIMENT-004C-C — Polymarket → SIG Cross-Venue Price Discovery | MERGED / ACCEPTED | `experiment/004c-c-crossvenue-price-discovery` | #37 | head `fbc8c2214969c8d9c7bd7afa7a7b20d248ecb742`; merge `751118f3edc818a113656e9a9f31951d29d195d3` | `INCONCLUSIVE / NO PROMOTED EDGE`; mapping-specific controls fail; CI green | merged 28 Sep 2026 |
| EXPERIMENT-004C-D — Conditional Response / Genuine Quote Renewal | MERGED / ACCEPTED | `experiment/004c-d-conditional-response-renewal` | #39 | head `3a27d1d54f392b860358f950e558ff5f7f433b70`; merge `6aa6a6c9f4108e582584bc141cc68fa5383e522c` | `NO_CONDITIONAL_EDGE`; final head includes CI-only full-history checkout fix; CI green | merged 28 Sep 2026 |
| EXPERIMENT-005A — Fee-Role Structure Discovery | IN REVIEW | `experiment/005a-fee-role-structure-discovery` | #38 | scaffold only; empirical gate remains closed | draft/open and intentionally unmerged; no empirical run in 004C close-out | open 28 Sep 2026 |

| BUILD-008 — Research evaluation harness | MERGED / ACCEPTED | `build/008-research-evaluation-harness` | #24 | head `9419a3bf478d834e86518f7a65ce5ea06d412eab`; merge `c56c225afbecc861fd440f961308992d436dfe84` | canonical research spec/run identity, as-of gate, validation/FDR/bootstrap/stability/ablation/execution-stress machinery accepted; synthetic machinery only, no alpha claim | merged 26 Sep 2026 |

## Closed / unmerged work worth remembering

- HIST-DATA-001 / PR #17 (`historical order-book and PolyLeviathan data lane`) was closed unmerged.
  It is not accepted capability on `main`. Historical data work remains useful, but the next
  implementation should reuse the accepted replay/data contracts rather than revive stale branch
  assumptions blindly.

GitHub merge state and the actual contents of `main` are authoritative when documentation drifts.
