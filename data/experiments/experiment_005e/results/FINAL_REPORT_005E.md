# EXPERIMENT-005E — Participant Ecology / Behavioural Atlas — Final Report

**Disposition:** NO_INCREMENTAL_EVIDENCE  
**Primary target:** 60-second future own-market canonical-YES logit change (y_60)  
**Pre-HOLDOUT freeze:** 60f642ec50a1a9ffa5aebfba73d8a66960f6afb2c318e7d747e584df81ca6de1

## Executive result

Participant-conditioned behavioural state does **not** add reliable out-of-sample information to the preregistered 60-second market-movement target beyond the generic price/size/activity baseline.

DEV selected two feature families — **specialisation** and **network/cross-market breadth** — but the sealed HOLDOUT gain was only 0.000124837276195 with bootstrap p=0.776 and BH-FDR q=1.0. The 95% block-bootstrap interval was [-0.001144713887169977, 0.0014520170381505239]. The effect was positive in 3/5 families but negative in Hungary and US_2024.

The broader full-behaviour model was worse than baseline on HOLDOUT (-0.000431715984), as were identity-history (-0.000319902409775), HGB full-behaviour (-0.00033201488022), and archetypes (-5.97039874811e-06).

## Data integrity and coverage

TRAIN/DEV audited 18,317,515 DATA-002 participant-side source rows, 8,238,705 transaction-condition groups and 10,071,855 reconstructed economic-trade rows. 2,017 transaction-condition groups failed the frozen conservation acceptance contract. US_2024 cleared the preregistered sample floor.

The sealed HOLDOUT evaluation used:
- TRAIN rows: 1,293,292
- DEV rows: 369,872
- HOLDOUT rows: 363,488
- primary baseline weighted MSE: 0.315063856892

Participant history is **family-local**. Cross-family conclusions below are model-transfer/stability statements, not a globally continuous wallet ledger.

## DEV discovery

The only feature families meeting the frozen DEV screen were:
- specialisation: DEV gain 0.000474141117332, positive in 3/5 families.
- network_cross_market: DEV gain 0.000450795445867, positive in 3/5 families.

Recurrence, historical markout, timing, directional behaviour and size behaviour failed the frozen DEV family-stability gate.

The frozen selected-behaviour model improved DEV MSE by 0.000556511238935. Full behaviour improved DEV by 0.000402827585508 but was negative in Hungary and US_2024. HGB and archetype challengers were worse than baseline on DEV.

## Primary sealed HOLDOUT

### Selected specialisation + cross-market breadth

- weighted MSE gain: 0.000124837276195
- bootstrap p-value: 0.776
- BH-FDR q-value: 1.0
- positive families: 3/5
- family gains: {"CAN_2025": 0.00012071006528319161, "COL_2026": 0.0002345734233112906, "HUN_2026": -4.683668763683091e-05, "PER_2026": 0.0018644566023463538, "US_2024": -0.0015487170223298097}
- effective contributing participants: 253.053
- top-participant effect share: 0.024558
- top-1% participant effect share: 0.729144

The slight aggregate gain is not statistically reliable. It is also not evidence of repeat-wallet skill: the repeat-participant slice gain is -5.19803276078e-05, while sparse/unseen rows account for the positive slices.

### Full behavioural state

- weighted MSE gain: -0.000431715984
- bootstrap p-value: 1.0
- BH-FDR q-value: 1.0
- positive families: 2/5
- family gains: {"CAN_2025": -0.0024813042187065704, "COL_2026": 0.00011607744724401181, "HUN_2026": -0.0008640469412944385, "PER_2026": 0.00297453433881284, "US_2024": -0.0019038405460571617}

Full behaviour is therefore rejected as an incremental 60-second price-prediction layer.

### Identity-history and archetypes

Identity-history gain is -0.000319902409775 with q=1.0. The six TRAIN-only descriptive archetypes have median bootstrap ARI 0.526706, but the archetype model's sealed primary gain is -5.97039874811e-06 with q=1.0. Neither supports a predictive claim.

## Falsification and transfer

The participant-state placebos are worse than baseline, but this cannot rescue a full-behaviour model that is itself negative versus baseline.

True leave-family-out full-behaviour gains are:
{"CAN_2025": {"gain": -0.0035924539684173973, "rows": 70644, "status": "OK"}, "COL_2026": {"gain": 0.0015179186010034762, "rows": 59404, "status": "OK"}, "HUN_2026": {"gain": -0.0009061779802632675, "rows": 70752, "status": "OK"}, "PER_2026": {"gain": 0.0012195898564668073, "rows": 73004, "status": "OK"}, "US_2024": {"gain": -0.022873647028763572, "rows": 89684, "status": "OK"}}

The broad transfer diagnostics are also weak:
- positive market share: 0.371314; median market gain -0.000627533033309
- positive event share: 0.321608; median event gain -0.00101764589202

This is not a stable cross-market or cross-event participant alpha.

## Secondary evidence worth carrying forward

The primary 005E hypothesis fails, but two **secondary, frozen** diagnostics are materially more interesting and should be treated as hypothesis generation only.

### Participant-direction-signed markout

Full behavioural state improves participant-direction-signed markout:
- 60s gain: 0.00924532001477, positive in all five families.
- 300s gain: 0.0123103062455, positive in all five families.

This suggests a narrower mechanism: behavioural state may help estimate whether the **current participant's observed direction** is likely to be followed by subsequent price movement, even though it does not improve unconditional future-price prediction. 005E does not provide the primary-target inference needed to promote this as alpha.

### Next price-change classification

The frozen full-behaviour classifier improves:
- log loss from 0.677277315061 to 0.666660658805 (gain 0.0106166562552)
- accuracy from 0.584708 to 0.604946.

Again, this is secondary evidence only. It needs a new preregistered experiment with its own nulls, calibration and executable mapping.

## Competition relevance

**Do not deploy 005E as a participant-conditioned 60-second price alpha.**

The only downstream proposal is a separate, tightly preregistered **participant-flow quality** follow-up centred on signed 60s/300s markout and next-change classification. It should:
1. make signed markout or next-change classification the primary target before new outcomes are inspected;
2. explicitly include current participant pressure/action direction in the executable mapping back to market direction;
3. retain nuisance-preserving participant-state permutations and true leave-family-out testing;
4. require broad transfer and execution/crossing economics;
5. use fresh/unconsumed data or live shadow capture rather than re-mining this HOLDOUT.

Until that passes, 005E contributes no production trading feature.

## Methodological cautions

- One address is an operational identifier, not necessarily one human.
- Unknown addresses are not assumed human.
- Co-occurrence is not coordination.
- Prior EXPERIMENT-003 participant evidence was contaminated by protocol-identity structure; 005E therefore avoids any trader-skill wording.
- Infrastructure exclusion was enforced, but the final DATA-002 participant population reported zero rows matching the current canonical infrastructure registry; that is a data fact, not proof that every remaining address is a human trader.
- Historical participant state is family-local, so cross-family recurrence itself is not directly measured.

## Final disposition

NO_INCREMENTAL_EVIDENCE on the preregistered primary target.

Secondary signed-flow/classification findings are FOLLOW_UP_ONLY; they are not promoted by this experiment.