# EXPERIMENT-004C-D — Post-Run Review

## Review disposition

The executed Kaggle v4 result reports `NO_CONDITIONAL_EDGE`. That label is acceptable only with a narrow scope:

> The frozen D1 age-conditioned magnitude model and D2 genuine-renewal timing model did not show a conditional predictive edge in the two adequately supported PRE challenge events, Colombia first round and Colombia runoff.

It must not be read as a universal no-edge conclusion across countries, mechanisms, horizons, or all challenge events.

## Evidence supporting the negative primary result

- All recorded v4 artifact hashes verify against `kaggle_run_summary.json`.
- Terminal freeze: `6adb6c2746fdba1453a5f845a515c9f56b5dab3b`.
- Implementation: `1e308d7cf16cdf17fcf8ca247000c14b758a7316`.
- Preregistration SHA-256: `9eafa50476ffa3e3d793081ead48e1536c02bed5a8571ca92c981158773b2688`.
- D1 PRE in both testable Colombia events had the wrong interaction sign and negative frozen squared-error gain.
- D2 PRE in both testable Colombia events had the wrong interaction sign and negative frozen Brier gain.
- Therefore C01-PRE and C02-PRE fail before multiplicity or positive-mechanism interpretation is needed; both Holm-adjusted slot p-values are 1.0.
- ACTIVE D1/D2 do not support the mechanism.
- C03 remained dormant under the frozen graph gate.
- C04 was activated by discovery support, but its testable PRE Colombia first-round result had the expected sign and negative predictive gain; Colombia runoff failed the added-feature support gate.

## Peru runoff limitation

Peru runoff PRE contains many valid target endpoints, but every confirmatory D1/D2 row is removed by the frozen model design because the event has only admitted `PRESIDENTIAL_RESULT` markets. The preregistered baseline requires varying outside-family common-state nuisance controls. Those controls are unavailable, so Peru runoff PRE is not testable under the frozen specification.

Accordingly:

- no Peru PRE confirmation exists;
- the preregistered country-generalisation condition is not met;
- row pooling across Colombia cannot substitute for Peru replication.

## Null and diagnostic limitations

The negative predictive result does not require a positive N2 interpretation. The following implementation limits must still be recorded:

1. D1's implemented N2 is a discovery residual block bootstrap preserving observed predictors/masks, not a full latent-price asynchronous replay. It is weaker than Astra's idealised common-information null and cannot establish causal information transmission.
2. The runner did not emit a separate preregistered N1 result or the fixed 600s block-sensitivity output.
3. The D2 sequential common-intensity null is materially miscalibrated in some challenge cells: simulated mean renewal rates differ substantially from observed rates. Under the preregistration, this prevents using N2 as a strong mechanistic explanation.
4. These null limitations do not rescue D1/D2, because the frozen challengers already fail the required expected-sign and held-out predictive-gain gates in the supported PRE challenge events.

## Observation-process controls

The raw-record D2 placebo improves more than the genuine-renewal interaction in Colombia first round, while the genuine D2 interaction itself worsens frozen Brier loss. This is consistent with caution around raw record/capture activity and does not provide support for genuine quote-renewal propagation.

## Final handoff language

Use:

> **NO_CONDITIONAL_EDGE within the adequately supported Colombia PRE evaluation for the frozen D1/D2 specifications.** No cross-country generalisation is available because Peru runoff PRE is untestable under the frozen outside-family-control requirement. No directional, causal-transmission, execution, or P&L claim is licensed.

Do not say:

- there is no conditional edge in prediction markets generally;
- Peru runoff confirmed the negative;
- N2 proved common information explains the data;
- D1/D2 tested price direction or trading profitability.