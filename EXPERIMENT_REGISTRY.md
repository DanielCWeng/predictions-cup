# Experiment Registry

This registry records canonical empirical status. A merged experiment can be a negative result; merge
status does not imply alpha, strategy promotion or execution readiness.

| Experiment | Question | Canonical status | Programme interpretation |
|---|---|---|---|
| EXPERIMENT-001A | Can public Polymarket data be captured reproducibly for research? | MERGED / ACCEPTED CAPTURE FOUNDATION | Read-only capture semantics; no strategy claim. |
| EXPERIMENT-002 | Can lead/lag, RV and leave-one-out relationships be evaluated under observable-time replay? | MERGED / ACCEPTED MACHINERY | Experiment machinery only; evidence depends on the dataset/run. |
| EXPERIMENT-003 | Do simple historical price/microstructure/participant relationship families survive controls? | COMPLETE / MOSTLY INCONCLUSIVE | Useful negative/prior evidence; not a production strategy set. |
| EXPERIMENT-004A / 004A.2 | What election-time regimes/event-time windows are empirically usable? | COMPLETE / ACCEPTED REGIME EVIDENCE | Supplies canonical regime semantics used by later work. |
| EXPERIMENT-004B | Is there non-random directed election-market structure? | COMPLETE / DISCOVERY EVIDENCE | System-level structure exists; individual transferable leader/follower identity was not established. |
| EXPERIMENT-004C-A | Does generic internal propagation survive freshness/activity/common-state controls? | COMPLETE / INCONCLUSIVE / NO PROMOTED EDGE | Do not promote generic internal pairwise lead-lag. |
| EXPERIMENT-004C-B | Does coherent/competitive redistribution predict correction? | COMPLETE / SOFT_COMPETITIVE_EFFECT_ONLY | One narrow Colombia PRE soft effect; no broad hard-family edge. |
| EXPERIMENT-004C-C | Does mapped Polymarket price discovery predict SIG repricing? | COMPLETE / INCONCLUSIVE / NO PROMOTED EDGE | Mapping-specific controls and economics do not support promotion. |
| EXPERIMENT-004C-D | Do age/activity/genuine-renewal interactions rescue 004B structure? | COMPLETE / NO_CONDITIONAL_EDGE | Frozen conditional mechanisms did not promote. |
| EXPERIMENT-005A | Does fee-role/aggressive-flow/participant structure add predictive information? | MERGED / NARROW SAME-FAMILY 5s EFFECT ONLY | Role annotation is usable; broad role-aware alpha is not supported. Carry one tiny PRE 5s same-family candidate for fresh replication only. |
| EXPERIMENT-005B | What does a broad identity-blind historical price/fill predictive atlas find, and does it transfer to the mapped 2026 universe? | CLOSED / HISTORICAL MOVEMENT SURVIVES ORDERING FALSIFICATION; FRESH DATA-003 TRANSFER FAILS 0/7 | Preserve the historical relationship as research evidence only. The seven frozen movement models do not transfer to DATA-003 and must not be treated as current SIG-universe predictive alpha. |
| EXPERIMENT-005C | Does joint/reduced-rank cross-market prediction add portable value? | MERGED / DOWNGRADE — FAMILYWISE NULL NOT REJECTED | Keep the strongest panel as shadow/research comparator only. |
| EXPERIMENT-005D | Do coherent structural/RV surfaces predict correction? | MERGED / NARROW LATE_COUNT STRUCTURAL EVIDENCE | Strong reconstruction; predictive evidence is weak/regime-specific. Peru weak dependence robustness; Colombia effect tiny. |
| EXPERIMENT-005E | Does participant ecology add incremental primary price prediction? | MERGED / NO_INCREMENTAL_EVIDENCE | Primary participant-behaviour hypothesis is negative; secondary diagnostics are follow-up-only. |
| EXPERIMENT-005F | Do rich microstructure/liquidity/event-time states add predictive information? | MERGED / QUALIFIED STATE-HAZARD SUPPORT | Persistent genuine economic-BBO age predicts update hazard in PRE/ACTIVE and ACTIVE jump hazard; not generic microstructure alpha. |
| MM-001 | Are selective passive market-making economics positive after fills/costs? | PLANNED BASELINE ENGINE / REQUIRES FILL MODEL | Belongs to MAKE. No passive fill model or executable maker claim yet. |
| LIVE-REPLICATION | Do accepted historical findings transfer to the actual mapped 2026 universe? | ACTIVE PROGRAMME / 005B TRANSFER NEGATIVE | DATA-003 is the preferred replication surface. 005B provides a completed negative transfer example: 0/7 frozen movement models pass. |

## Registry discipline

- The approved monetisation families are `FV-TAKE`, `MAKE`, `STRUCT`, `PRED`, `EVENT` and
  `NO_TRADE`.
- Findings such as staleness, flow quality, volatility, liquidity or participant ecology are normally
  features/challengers within those families.
- Chronological TRAIN/DEV/HOLDOUT boundaries, purge/embargo and preregistered multiplicity controls
  remain binding.
- Once HOLDOUT is consumed, follow-up analysis may falsify/narrow but may not redesign to rescue.
- Historical success is not equivalent to actual-2026-universe validation.
- Maker fills, queue position, autonomous execution and portfolio/risk decisions remain outside the
  accepted experiment runtime unless explicitly implemented and reviewed.
