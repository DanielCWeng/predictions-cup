# HISTORICAL ELECTION ANALOGUES 2024–2026

**Research package:** RESEARCH-PACKAGE-001  
**Repository:** `DanielCWeng/predictions-cup`  
**Purpose:** preserve the completed historical-election analogue research as a canonical research/data package.

## Scope

This report asks whether recent historical prediction-market elections create useful **statistical or market-structural analogues** for the 2026 SIG Predictions Trading Cup.

It does **not** claim exact current instrument identity. That belongs to MAPPING-001.

The analogue types are kept separate:

- **semantic analogue** — similar payoff/resolution object;
- **microstructure analogue** — historical data can reproduce relevant order-book/trade mechanics;
- **liquidity analogue** — similar depth/spread/participation; this was not established robustly by the completed research and is therefore generally left unscored;
- **event-timing analogue** — similar event-night/staged-information timing problem;
- **cross-market structural analogue** — similar constituent→aggregate, seat-distribution→control, joint, conditional or staged-market graph.

Historical evidence is for experiment design, structural hypotheses, regime definitions and stress tests. **Live 2026 SIG data remains authoritative for venue-specific production calibration.**

## Critical provenance correction: 2024/25 Polymarket fills

The working research previously cited an external Polymarket fill archive covering hundreds of millions of fills. That source is **not used in this canonical package** because it materially undercounted 2024/25 fills.

Consequences:

1. 2024/25 elections remain valid **semantic and structural** research examples.
2. They are **not** certified B-grade fill replays in this package.
3. Their default package grade is **C** unless another market-specific source independently supports a higher grade.
4. Any trade/fill replay for those elections must use a separately extracted and validated **Polyleviathan-derived** fill source (or another verified market-specific source).
5. The package contains no such Polyleviathan extract, so it does not claim it has been validated here.

This correction does not affect the independently identified 2026 pmxt/Joseph order-book datasets.

## Data-quality framework

- **A** — true event-time/full-book replay is supportable for the relevant market family, subject to exact-ID and continuity checks.
- **A/B mixed** — some election phases have true book data while another phase is less complete or gated.
- **C with A candidate** — price/metadata is usable; an A-grade replay is possible only if exact archived conditions/tokens and continuity are confirmed.
- **C** — price/metadata/structural study only in this package.
- **D** — reference/metadata only.

### Full-book limitation

Historical trade/fill data without resting-order and cancellation history is **not** a historical full-order-book replay. Without book-event history, queue position cannot be reconstructed, cancellation behaviour is unknown, maker fills cannot be simulated faithfully, and passive market-making backtests are invalid. Trade/price/event studies remain possible at the granularity actually available.

## Elections examined

| Election ID | Election | Date(s) | Canonical package data quality | Research decision | Why included / relevance |
| --- | --- | --- | --- | --- | --- |
| USA_2024_GENERAL | 2024 U.S. federal general election | 2024-11-05 | C | ACCEPT | Closest semantics for Senate races, chamber control, seat aggregation and joint control. |
| USA_2025_STATE | 2025 U.S. state election family | 2025-11-04 | C | ACCEPT | Best direct historical analogue for SIG gubernatorial/statewide races; useful joint-market exercises. |
| CAN_2025_FEDERAL | 2025 Canadian federal election | 2025-04-28 | C | ACCEPT | Best recent non-U.S. constituent→seat-total→government-control laboratory found. |
| DEU_2025_FEDERAL | 2025 German federal election | 2025-02-23 | C | ACCEPT | Excellent high-liquidity aggregate-party benchmark; weaker for individual-race topology. |
| AUS_2025_FEDERAL | 2025 Australian federal election | 2025-05-03 | C | ACCEPT | Useful seat-bin→government and most-seats→government relative-value analogue. |
| POL_2025_PRES | 2025 Polish presidential election | 2025-05-18; 2025-06-01 | C | ACCEPT | Good two-stage winner benchmark; Peru/Colombia are stronger because their 2026 book coverage is richer. |
| BOL_2025_PRES | 2025 Bolivian presidential election | 2025-08-17; 2025-10-19 | C | ACCEPT | Strong historical test of conditional probability and information migration across linked staged markets. |
| JPN_2025_COUNCILLORS | 2025 Japan House of Councillors election | 2025-07-20 | C | ACCEPT | Separates total-seat control from seats-won-in-this-election; useful semantic-resolution stress test. |
| NOR_2025_PARL | 2025 Norwegian parliamentary election | 2025-09-08 | C | ACCEPT_SECONDARY | Useful party-most-seats control case but inferior topology to Canada/Hungary. |
| CZE_2025_PARL | 2025 Czech parliamentary election | 2025-10-03 to 2025-10-04 | C | ACCEPT_SECONDARY | Useful threshold/bin laboratory, especially for residual tests among related popular-vote propositions. |
| NLD_2025_PARL | 2025 Dutch parliamentary election | 2025-10-29 | C | ACCEPT | Good range/bin→aggregate test and seat-distribution semantic benchmark. |
| CHL_2025_PRES | 2025 Chilean presidential election | 2025-11-16; 2025-12-14 | C | ACCEPT_SECONDARY | Additional staged-election robustness check. |
| ECU_2025_PRES | 2025 Ecuadorian presidential election | 2025-02-09; 2025-04-13 | C | ACCEPT_SECONDARY | Conditional first-round/overall relationship; useful robustness check. |
| HUN_2026_PARL | 2026 Hungarian parliamentary election | 2026-04-12 | C_WITH_A_CANDIDATE | ACCEPT_GATED | Excellent market-structural analogue for vote→seat bins→most-seats and aggregate lead/lag. |
| PER_2026_GENERAL | 2026 Peruvian general election | 2026-04-12; 2026-06-07 | A/B_MIXED | ACCEPT | Best cross-institution + staged-election graph found in the 2026 archive window. |
| COL_2026_PRES | 2026 Colombian presidential election | 2026-05-31; 2026-06-21 | A | ACCEPT | Cleanest A-grade staged-event microstructure laboratory for event-night lead/lag and LOO-family tests. |
| SWE_2026_PARL | 2026 Swedish general election | 2026-09-13 | A_RAW_LOW_UTILITY | REJECT_FIRST_WAVE | Useful only as a low-liquidity stress test; not a first analogue. |
| KOR_2025_PRES | 2025 South Korean presidential election | 2025-06-03 | C | REJECT_FIRST_WAVE | High liquidity but weak graph topology for SIG constituent/aggregate research. |
| IRL_2025_PRES | 2025 Irish presidential election | 2025-10-24 | C | REJECT_FIRST_WAVE | Some event-time value but structurally weaker than staged Peru/Colombia/Bolivia. |
| CRI_2026_PRES | 2026 Costa Rican presidential election | 2026-02-01 | C | REJECT_FIRST_WAVE | Available but dominated by richer staged-election families. |
| GBR_2024_GENERAL | 2024 U.K. general election | 2024-07-04 | C | REFERENCE_ONLY | Useful fallback for seat-distribution work if a 2025/26 sample proves insufficient. |

The U.K. 2024 election is retained as a reference-only case because it falls outside the requested 25 September 2024 start window.

## Key analogue families

| Historical family | Semantic analogue | Microstructure analogue | Liquidity analogue | Event-timing analogue | Cross-market structural analogue | Recommended use |
| --- | --- | --- | --- | --- | --- | --- |
| U.S. 2024 federal | High | Not certified from this package | Not separately established | High | High | Closest semantic benchmark for individual Senate races, House/Senate control, seat brackets and joint control. Trade-level replay is gated on verified Polyleviathan fills. |
| U.S. 2025 state | High for gubernatorial/statewide | Not certified from this package | Not separately established | High | Medium | Direct U.S. statewide-winner benchmark; useful joint/sweep structures. Historical fill replay is data-gated. |
| Canada 2025 federal | Medium | Not certified from this package | Not separately established | Medium | High | Strong constituent/seat-distribution/government graph. Use for structural tests; fill-level claims require verified Polyleviathan extraction. |
| Hungary 2026 parliamentary | Medium | Gated v1 book candidate | Not separately established | High | High | Excellent popular-vote→seat-bin→most-seats graph; only promote to book replay after exact CID/token archive verification. |
| Peru 2026 general | Low-to-medium | High for runoff/final; first round gated | Not separately established | High | High | Rich staged presidential plus legislative family; strongest cross-institution experiment set. |
| Colombia 2026 presidential | Low-to-medium | High true-book replayability | Not separately established | High | Medium-to-high | Cleanest A-grade staged-election microstructure laboratory; not a semantic clone of U.S. midterms. |

### U.S. 2024 federal

This remains the strongest **semantic** benchmark in the research window:

```text
competitive state Senate races
          ↓
     Senate control

House-race / seat information
          ↓
  House seat brackets
          ↓
      House control

House control + Senate control
          ↓
Balance-of-Power joint state
```

Recommended use:
- individual Senate race ↔ aggregate Senate-control hypotheses;
- House seat-distribution ↔ House-control consistency;
- joint dependence / correlation structure.

Limitation: in this package the Polymarket side is structural/price evidence only unless verified Polyleviathan fills are separately extracted. The post-election exact-House-seat market must never be used as a pre-election predictor.

### Canada 2025 federal

```text
selected constituency / regional outcomes
                 ↓
          party seat distribution
                 ↓
       majority/minority government
```

This is the strongest recent non-U.S. **cross-market structural** analogue for constituency→seat-distribution→aggregate-control research.

Limitation: the listed historical prediction markets do not form a complete one-market-per-riding clone of the SIG universe. Unlisted constituencies must not be imputed as if they traded.

### Hungary 2026 parliamentary

```text
national-list popular-vote winner
              ↓
       party seat-count bins
              ↓
       party wins most seats
              ↓
        next prime minister
```

Recovered IDs include:
- Fidesz–KDNP most-seats market `948038`, condition `0xa312499c150a1ca94b788c99f6ae721cbbdfda7679b5d25580fe79d514fdb930`;
- TISZA 70–79 seats market `1570162`, condition `0xcb951bbdbb2bbc31803745656c98fb1cf0c0d4243c2650418e0f452ce35aae7b`;
- popular-vote-winner event `246787`;
- Fidesz seat-count event `263762`, representative child market `1570885`, condition `0x14e084f7ef784233a8f42ce3bc24826b958798bca1dbf009bbe169535cf2a170`.

Hungary is structurally Tier 1 but remains **C with an A-grade candidate** because pmxt v1 did not cover every market. Exact condition/token presence and continuity must be verified before any book replay.

### Peru 2026 general

Peru provides the richest combined staged/cross-institution graph:

```text
first-round presidential winner
        ↓
runoff qualification / runoff pair
        ↓
overall presidential winner

Senate outcomes ──────┐
                      ├── broader election state
Chamber outcomes ─────┘
```

The runoff/final phase is the strong A-grade book-replay segment. The April 12 first round remains gated on exact v1 CID/token presence.

### Colombia 2026 presidential

Colombia is the cleanest A-grade **microstructure/event-timing** laboratory:

```text
23-outcome first-round candidate family
                   ↓
             first-round result
                   ↓
               runoff/final
                   ↓
         overall-winner family
```

Recovered identifiers include first-round event `34582`, exact child market/condition/token identifiers for several candidates, and overall event `34584`.

Recommended use:
- event-time lead/lag;
- LOO-family indirect fair value;
- stale-related-market detection;
- selective market-making only after market-level book continuity/liquidity checks.

The inactive Colombia Senate market is not treated as an economically useful predictor merely because it is semantically related.

## Analogue matrix

The canonical machine-readable analogue matrix is `data/research/historical_elections/analogue_matrix.csv`.

Important interpretation:

- component scores are preserved from the original research;
- no new composite similarity score is introduced;
- liquidity similarity is generally `NOT_SEPARATELY_ESTABLISHED`;
- `historical_market_id` is intentionally blank for family-level analogues where no exact historical market identity was established;
- an analogue row is **not** an exact SIG↔external mapping.

## Replay experiments

| ID | Historical family | Hypothesis | Data quality / gate | Priority |
| --- | --- | --- | --- | --- |
| BT-01 | USA_2024_GENERAL: Senate control | Constituent-market repricing may contain information that appears later in the aggregate market. | C in this package; trade/fill level requires verified Polyleviathan extract; DATA_GATED | Highest |
| BT-02 | USA_2024_GENERAL: Held-out Senate race | Aggregate-market repricing may contain information that appears later in a held-out constituent market. | C in this package; trade/fill level requires verified Polyleviathan extract; DATA_GATED | Highest |
| BT-03 | USA_2024_GENERAL: House control | A contemporaneous seat-distribution surface may imply a control probability that differs from the control market and later converges. | C in this package; trade/fill level requires verified Polyleviathan extract; DATA_GATED | Highest |
| BT-04 | USA_2024_GENERAL: Balance-of-Power joint | Standalone chamber markets and a joint market may imply measurable, time-varying dependence residuals. | C in this package; trade/fill level requires verified Polyleviathan extract; DATA_GATED | High |
| BT-05 | CAN_2025_FEDERAL: Seat bins / next government | Local-market repricing may lead national seat/government repricing, or vice versa. | C in this package; fill replay not certified; DATA_GATED | Highest |
| BT-06 | CAN_2025_FEDERAL: Next Government of Canada | Seat-bin probability mass may imply a government/control probability that later converges with the quoted aggregate. | C in this package; fill replay not certified; DATA_GATED | High |
| BT-07 | HUN_2026_PARL: TISZA/Fidesz seat bins | Popular-vote/party-winner information may lead party seat-bin repricing. | C / A candidate; DATA_GATED | Gated |
| BT-08 | HUN_2026_PARL: Party most-seats winner | Party seat-bin surfaces may contain information that appears later in the headline most-seats market. | C / A candidate; DATA_GATED | Gated |
| BT-09 | COL_2026_PRES: Overall candidate winner | First-round candidate-market moves may precede repricing in the overall-winner family during event-time regimes. | A; READY_AFTER_MARKET_CONTINUITY_CHECK | Highest |
| BT-10 | COL_2026_PRES: Held-out overall candidate | The non-mechanical remainder of the election graph may predict subsequent repricing of a held-out target. | A; READY_AFTER_MARKET_CONTINUITY_CHECK | Highest |
| BT-11 | COL_2026_PRES: Liquid candidate legs | Some liquid candidate legs may support positive passive spread capture after adverse selection and inventory costs. | A; READY_AFTER_MARKET_CONTINUITY_CHECK | High |
| BT-12 | PER_2026_GENERAL: Overall president winner | Runoff qualification/pair markets may lead overall-winner repricing. | A final phase; first round gated; READY_FINAL_PHASE__GATED_FIRST_ROUND | Highest |
| BT-13 | PER_2026_GENERAL: Presidential target or legislative target | Markets from another institution may contain non-mechanical information about a held-out presidential or legislative target. | A final phase; first round gated; READY_FINAL_PHASE__GATED_FIRST_ROUND | High |
| BT-14 | BOL_2025_PRES: Overall winner | Staged-election lead/lag relationships may persist in another election family. | C in this package; fill replay not certified; DATA_GATED | Medium |
| BT-15 | AUS_2025_FEDERAL: Next government | Aggregate consistency relationships may survive a different parliamentary setting. | C in this package; fill replay not certified; DATA_GATED | Medium |
| BT-16 | NLD_2025_PARL: Party seat bins / most-seats family | Residual relationships may persist across low-liquidity party range markets. | C in this package; fill replay not certified; DATA_GATED | Medium |

### First-wave ordering after the fill-source correction

1. **Colombia 2026** — first executable A-grade order-book programme.
2. **Peru 2026 runoff/final** — A-grade staged/cross-institution programme.
3. **U.S. 2024 Senate / House graph** — highest semantic priority, but trade-level replay is **data-gated** until verified Polyleviathan-derived fills are extracted.
4. **Canada 2025** — high structural priority, likewise data-gated for trade-level replay.
5. **Hungary 2026** — promote only if exact pmxt v1 condition/token coverage passes.

The U.S. 2024 programme remains strategically important; the correction changes its **data readiness**, not its semantic value.

## No-lookahead rules

Historical backtests must behave as though the strategy were present at the time. They must not use:

- final resolution;
- later candidate withdrawal knowledge;
- final vote or seat totals;
- data timestamped after the simulated decision;
- later market membership;
- post-election markets to construct pre-election signals.

The U.S. 2024 exact-House-seat market identified as opening after election day is therefore excluded from pre-election signalling.

## Recommended use versus production calibration

Use the historical package to:
- design experiments;
- choose candidate horizons and regime splits;
- understand constituent↔aggregate graph structure;
- test staged-election information migration;
- define stress cases;
- identify where lead/lag hypotheses are worth attempting.

Do **not** automatically transfer historical coefficients into production. The following remain 2026-SIG-specific:
- spread/queue assumptions;
- order latency;
- fill probability;
- cancellation rates;
- depth and liquidity;
- adverse-selection parameters;
- risk limits;
- venue-specific calibration.

## Canonical machine-readable artefacts

- `data/research/historical_elections/election_catalogue.json`
- `data/research/historical_elections/market_catalogue.csv`
- `data/research/historical_elections/analogue_matrix.csv`
- `data/research/historical_elections/replay_experiment_matrix.csv`
- `data/research/historical_elections/README.md`

No production strategy code is part of RESEARCH-PACKAGE-001.