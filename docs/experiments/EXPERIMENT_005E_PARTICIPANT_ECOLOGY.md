# EXPERIMENT-005E — Participant Ecology / Behavioural Atlas

## Objective

Test whether **past-observable participant behaviour** adds predictive information about subsequent market state beyond generic price, size and activity controls. The predictive object is behavioural state, not a wallet leaderboard.

## Contamination boundary

The protocol was frozen after reading only Layer-1 material allowed by `docs/research/005_DISCOVERY_PRIOR_WORK_BOUNDARY.md`: field semantics, data limitations, infrastructure issues, methodology, MATHS_LEDGER and DATA-001/DATA-002 contracts. No outcome-ranked prior participant table, best wallet, best participant horizon or post-hoc identity list was intentionally inspected before this freeze.

## Data grain

DATA-002 participant-side `OrderFilled` rows are the participant observation grain. Market targets are reconstructed from validated passive rows only, after transaction-condition conservation checks, so the active audit row is never double-counted as an independent market trade. Canonical infrastructure addresses are excluded from participant claims. Unknown addresses are not assumed human.

DATA-002 participant timestamps are one-second block-time proxies. Participant history therefore uses only rows with timestamps **strictly earlier** than the current row. `tx_hash` and `log_index` are never used to invent within-second information ordering. Same-second rows are treated as one observable batch for evidence weighting.

Participant state is constructed independently inside each frozen family file. Accordingly, cross-event recurrence means recurrence across `event_id`/markets represented within that family; cross-family evidence means model transfer/stability across the five family datasets, not a globally continuous wallet ledger spanning overlapping family files.

## Split / freeze

Each family uses a timestamp-only 70% TRAIN / 15% DEV / 15% HOLDOUT chronology with 300-second boundary embargo and target-interval purge. DEV selects the frozen participant-family shortlist and hyperparameters. HOLDOUT is then evaluated once. Earlier HOLDOUT outcomes may only become later strictly-past participant history through the preregistered online feature construction; they never trigger model redesign.

## Primary question

The single primary response is 60-second future own-market canonical-YES logit change. The generic baseline controls current probability, recent returns, recent anonymous economic-trade counts/value, current participant-row size and broader event activity. Participant ecology must improve on that baseline to count as incremental information.

## Feature atlas

Frozen families: recurrence/persistence, market/event specialisation, size behaviour, owner-direction behaviour, timing/activity state, shrunk past-only historical markout, and participant↔market/cross-market breadth. Historical markout uses a neutral prior strength of 20 and cannot enter a row until its 300-second label is fully observable. Unseen wallets receive neutral state.

## Behavioural fingerprints

TRAIN-only latest participant timestamp states with >=20 prior fills and >=2 prior active days are standardized, projected with TRAIN-only PCA, and clustered with six MiniBatchKMeans centroids fixed ex ante. If a participant has multiple rows in their latest second, fingerprint features are averaged across that entire timestamp batch; tx/log ordering is never used to choose a row. DEV/HOLDOUT are assigned through the frozen TRAIN transform. Sparse participants receive an explicit sparse archetype rather than being forced into a learned cluster. Cluster stability is reported with deterministic bootstrap adjusted-Rand scores.

## Model and selection policy

Ridge is the main interpretable ladder. The frozen alpha grid is 0.1/1/10/100. A participant feature family survives DEV only if it improves weighted primary-target MSE overall and in at least three of the five data families. Evidence weights equalise family → market → observable timestamp batch → participant row, preventing a same-second burst of fills from masquerading as many independent confirmations. Identity-history, full-behaviour and archetype variants are always retained as predeclared comparators. A small HGB challenger and L2 logistic next-change diagnostic are secondary. Raw p-values do not select features.

## Falsification and concentration

Positive results face participant-state permutation/pseudo-state controls, infrastructure exclusion, delayed/shuffled history controls, activity/size/frequency controls, market/family transfer diagnostics and deterministic concentration removals. Every primary candidate reports effect concentration in the top participant, top 5, top 10 and top 1%, effective participant count, and unseen/sparse/repeat-wallet performance. Co-occurrence is never labelled coordination.

## Interpretation

Allowed scientific handoff labels are `NO_INCREMENTAL_EVIDENCE`, `REGIME_SPECIFIC_CANDIDATE`, `CROSS_FAMILY_CANDIDATE`, or `INCONCLUSIVE`. None means a named wallet is "smart" and none authorizes live orders.
