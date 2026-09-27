# EXPERIMENT-004B-B — Dynamics, Nulls & Stability findings

- Frozen base: `e9a95fb2092c7e7385ecf27f6972b8d8921c1f72` / tree `462b4589a13782662b69db879f77e8ba22d8a9ab`.
- Kaggle v3: `COMPLETE`; discovery spec hash matches; sealed empirical loads = `0`; every downloaded output hash matches `kaggle_run_summary.json`.
- Full directed matrix: `585,900` rows, no duplicate cell keys, fixed resolutions/horizons retained, and unsupported cells remain explicit.

## Lead/lag integrity

- Static runner audit: source state is taken at `t`; price response is `mid[t+h] - mid[t]`; therefore the response interval starts after the shock timestamp and no future endpoint enters the predictor. Panels are constructed separately by discovery event and primary regime before response/null routines run.
- 1s/5s grids use the same strict positive-lag slicing. Unsupported horizon/resolution combinations are emitted rather than dropped. Quotes are as-of sampled with the frozen freshness rule; there is no interpolation through missing points.
- Standardisation is descriptive within the same discovery event/regime aligned sample. No sealed/holdout event contributes. The OFI-labelled lane is the frozen `depth_imbalance_change` proxy; source-side is not treated as aggressor direction.

## Directed response coverage

- `price -> future price`: 6,254 available cells / 58,590 preregistered cells.
- imbalance/OFI-proxy `-> future price`: 3,351 / 58,590.
- `price -> future imbalance/OFI-proxy`: 3,114 / 58,590.
- liquidity-depth change `-> future price`: 3,351 / 58,590.
- BBO-activity and depth-activity `-> future price`: 14,350 / 58,590 each; fill-count and fill-notional variants: 9,350 / 58,590 each. Venue-trade count/notional response cells are all unavailable under the frozen overlap/variation rules and remain explicit rather than being dropped.

## Canonical 30s null evidence

- `hungary_election / PRE_ELECTION / NULL_A_CIRCULAR_SHIFT`: observed mean-|corr| `0.0305821`, null mean `0.005337`, p `0.000999001`, q `0.001332` (BH<=0.05); valid draws `1000/1000`.
- `hungary_election / PRE_ELECTION / NULL_B_BLOCK_PERMUTATION`: observed mean-|corr| `0.0305821`, null mean `0.00562823`, p `0.000999001`, q `0.001332` (BH<=0.05); valid draws `1000/1000`.
- `hungary_election / ACTIVE_RESULTS / NULL_A_CIRCULAR_SHIFT`: observed mean-|corr| `0.107461`, null mean `0.0950399`, p `0.345324`, q `0.345324` (not BH<=0.05); valid draws `833/1000`.
- `hungary_election / ACTIVE_RESULTS / NULL_B_BLOCK_PERMUTATION`: observed mean-|corr| `0.107461`, null mean `0.0599607`, p `0.148851`, q `0.198468` (not BH<=0.05); valid draws `1000/1000`.
- `peru_first_round / PRE_ELECTION / NULL_A_CIRCULAR_SHIFT`: observed mean-|corr| `0.0137481`, null mean `0.00885669`, p `0.000999001`, q `0.000999001` (BH<=0.05); valid draws `1000/1000`.
- `peru_first_round / PRE_ELECTION / NULL_B_BLOCK_PERMUTATION`: observed mean-|corr| `0.0137481`, null mean `0.00896103`, p `0.000999001`, q `0.000999001` (BH<=0.05); valid draws `1000/1000`.
- `peru_first_round / ACTIVE_RESULTS / NULL_A_CIRCULAR_SHIFT`: observed mean-|corr| `0.0488162`, null mean `0.0328895`, p `0.00599401`, q `0.00599401` (BH<=0.05); valid draws `1000/1000`.
- `peru_first_round / ACTIVE_RESULTS / NULL_B_BLOCK_PERMUTATION`: observed mean-|corr| `0.0488162`, null mean `0.0334988`, p `0.002997`, q `0.003996` (BH<=0.05); valid draws `1000/1000`.

Only the preregistered aggregate 30s `PRICE_STRUCTURE` statistics have empirical null p/q values. Individual directed cells, OFI/liquidity/activity families, and other horizons are descriptive; no cell-level multiplicity-survival claim is supported by v3.

## Stability

- PRE and ACTIVE are never pooled. `stability_audit_summary.csv` compares exact shared ordered condition pairs at the fixed 30s resolution for every supported horizon (30/60/120/300) and every directed response family.
- Peru R1 price->price PRE-vs-ACTIVE sign agreement is 0.486-0.556 across those horizons and effect correlation ranges from -0.276 to 0.046; the other principal response families are likewise weak/inconsistent rather than stable. Hungary has only two comparable ordered edges, so its +/-1 correlations are not interpretable as robust stability evidence.
- Cross-event HUN vs Peru R1 directed-effect stability is marked `INSUFFICIENT_COMPARABLE_TOPOLOGY`: the admitted universes do not share an exact market-family topology. This is `CROSS_DISCOVERY_EVENT_STRUCTURE` evidence only, not broad replication.

## Matched non-edge controls

- v3 contains four matched non-edge rows, all outcome-independent in implementation. One Peru PRE control has no same-family match; the three Peru ACTIVE controls do. These controls have no empirical p-values and are not part of BH claims.

## Blocker / limitation

- The runner made 1,000 deterministic permutation attempts, but two Hungary ACTIVE circular-shift statistics had fewer than 1,000 valid draws: CONTEMPORANEOUS_MEAN_ABS_PRICE_CORR=648/1000, DIRECTED_30S_RESPONSE_MEAN_ABS_CORR=833/1000. The frozen brief states an expected 1,000 draws. This lane did not rerun or change the null implementation; MASTER must disposition this before scientific acceptance.

No strategy, P&L, threshold optimisation, or 004C hypothesis generation was performed.
