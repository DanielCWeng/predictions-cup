# EXPERIMENT-005 Discovery Prior-Work Boundary

This document is a contamination-control convention for the broad discovery lanes that may follow EXPERIMENT-005A. It is not a security boundary, and it does not delete, hide, or invalidate historical evidence.

The purpose is to let new agents avoid duplicate work while reducing anchoring to prior result magnitudes before their own discovery protocols are frozen.

## Layer 1 — permitted before each new lane freezes its design

Before its own design freeze, a new discovery agent may inspect:

- data and provenance limitations;
- available datasets;
- regime definitions;
- feature semantics;
- experiment methodology;
- which broad families have already been attempted;
- known implementation defects and redundancies;
- whether a specific idea has already been tested;
- `MATHS_LEDGER.md`;
- DATA-001 / DATA-002 semantics.

Methodological warnings that are safe to carry forward before freeze:

- EXPERIMENT-003 lead-lag was a narrow 30-second-grid / 5-second-lookback specification, not a broad census of event-time information propagation;
- the primitive implemented microprice displacement was algebraically redundant with imbalance;
- the participant experiment was confirmatorily invalidated and must not be treated as evidence of trader-skill predictability;
- earlier low-rank work was rank-1/rank-2 and severely underpowered;
- EXPERIMENT-004C tested specific identification mechanisms rather than a broad predictor census.

Agents may also use high-level dispositions to avoid exact duplication, provided they do not inspect outcome-ranked cells or magnitudes.

## Layer 2 — embargoed until the new lane's own discovery protocol is frozen

Before its own design freeze, a new discovery agent should **not intentionally inspect**:

- detailed effect tables;
- best-performing individual contracts or pairs;
- coefficient magnitudes;
- best horizons;
- subgroup winners;
- prior "interesting cell" rankings;
- post-hoc candidate lists;
- detailed result CSVs whose purpose is outcome inspection.

The agent should first freeze, at minimum:

- feature universe;
- targets;
- TRAIN / DEV / HOLDOUT rules;
- model ladder;
- screening policy.

After that freeze, the agent may inspect full historical evidence for comparison, duplication avoidance and interpretation.

Prior outcomes must not then be used to modify the frozen search universe, targets, model ladder, screening policy or holdout rules. Any scientifically necessary amendment must be explicit, separately justified and provenance-preserving.

## Practical interpretation

This convention is designed to prevent a new lane from becoming a disguised rescue search over old winners while still allowing it to learn from prior engineering mistakes, data defects and already-tested broad families.

Historical evidence remains canonical and available. The sequencing rule is simply: **design first, detailed prior outcomes second**.
