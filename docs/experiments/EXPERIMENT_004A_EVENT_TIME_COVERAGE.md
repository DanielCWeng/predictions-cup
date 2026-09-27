# EXPERIMENT-004A — Event-Time Coverage & Regime Validation

> Evidence validation only. No alpha search, parameter optimisation, strategy simulation, execution simulation, or P&L is performed by this package.

- Regime package: `004A-event-time-v2`
- Regime package SHA-256: `57a102d64778be7c1460638bb4da63be7eeafe0b9494a4287339674bcad0a741`
- DATA-001 verification: `115 files / OK`
- Historical venue: Polymarket only. No historical SIG observations are claimed.

## Frozen coverage-sufficiency rule

PRE_ELECTION ends at factual poll open and requires an initialized depth state at least 6h before poll open, at least 20 book observations, and fresh checkpoint evidence (depth age <= 30m; straddling gap <= 60m). ACTIVE_RESULTS is the predeclared 6h window after first meaningful results and requires initialization no later than result onset and coverage for the full active window when it is shorter than 3h, otherwise at least the first 3h. Token-level silence is descriptive because unchanged state can persist; a regime-wide source silence longer than 30 minutes hard-fails usability.

The rule is fixed independently of all later alpha outcomes.

## Colombia first round

| Anchor | Local | UTC | Evidence |
|---|---|---|---|
| Poll open | 2026-05-31T08:00:00-05:00 | 2026-05-31T13:00:00Z | Registraduria Nacional del Estado Civil |
| Poll close | 2026-05-31T16:00:00-05:00 | 2026-05-31T21:00:00Z | Registraduria Nacional del Estado Civil |
| First meaningful results | 2026-05-31T16:11:00-05:00 | 2026-05-31T21:11:00Z | El Espectador live results |
| ACTIVE_RESULTS end | 2026-05-31T22:11:00-05:00 | 2026-06-01T03:11:00Z | EXPERIMENT-004A frozen event-time policy |
| LATE_COUNT end | 2026-06-05T00:00:00-05:00 | 2026-06-05T05:00:00Z | Consejo Nacional Electoral Resolution E-2838 |

DATA-001 window: `2026-05-29T00:00:00Z` → `2026-06-03T00:00:00Z`. Candidate tokens: 472; tokens with book evidence: 260.

Usability: BOTH 14; PRE_ELECTION_ONLY 4; ELECTION_NIGHT_ONLY 29; NEITHER 425.

Special anchor: **RESULTS_MILESTONE** at 2026-05-31T17:32:00-05:00 (2026-05-31T22:32:00Z). With more than 97% of polling tables reported, the live timeline records the two runoff qualifiers as confirmed; later scrutiny remained legally relevant.

Sources:
- Registraduria Nacional del Estado Civil: https://www.registraduria.gov.co/Elecciones-2026
- El Espectador live results: https://www.elespectador.com/politica/resultados-elecciones-presidenciales-colombia-2026-en-vivo-quien-gano/
- Consejo Nacional Electoral Resolution E-2838: https://www.cne.gov.co/publicaciones-generales-2026/cne-dgc-aceg-029375-2026-gen-r

## Colombia runoff

| Anchor | Local | UTC | Evidence |
|---|---|---|---|
| Poll open | 2026-06-21T08:00:00-05:00 | 2026-06-21T13:00:00Z | Registraduria Nacional del Estado Civil |
| Poll close | 2026-06-21T16:00:00-05:00 | 2026-06-21T21:00:00Z | Registraduria Nacional del Estado Civil |
| First meaningful results | 2026-06-21T16:11:00-05:00 | 2026-06-21T21:11:00Z | El Espectador live results |
| ACTIVE_RESULTS end | 2026-06-21T22:11:00-05:00 | 2026-06-22T03:11:00Z | EXPERIMENT-004A frozen event-time policy |
| LATE_COUNT end | 2026-06-25T00:00:00-05:00 | 2026-06-25T05:00:00Z | Consejo Nacional Electoral Resolution E-3181 |

DATA-001 window: `2026-06-19T00:00:00Z` → `2026-06-24T00:00:00Z`. Candidate tokens: 472; tokens with book evidence: 84.

Usability: BOTH 6; PRE_ELECTION_ONLY 0; ELECTION_NIGHT_ONLY 2; NEITHER 464.

Special anchor: **RESULTS_MILESTONE** at 2026-06-21T17:27:00-05:00 (2026-06-21T22:27:00Z). At 17:27 the Registraduria pre-count had reached 99% of polling tables; because the margin remained close, subsequent scrutiny is classified as LATE_COUNT rather than informationally ignored.

Sources:
- Registraduria Nacional del Estado Civil: https://www.registraduria.gov.co/Elecciones-2026
- El Espectador live results: https://www.elespectador.com/politica/elecciones-colombia-2026/resultados-elecciones-en-colombia-2026-en-vivo-asi-va-el-preconteo/
- Consejo Nacional Electoral Resolution E-3181: https://www.cne.gov.co/resoluciones-cne-2026/cne-dgc-aceg-029557-2026-gen

## Peru first round

| Anchor | Local | UTC | Evidence |
|---|---|---|---|
| Poll open | 2026-04-12T07:00:00-05:00 | 2026-04-12T12:00:00Z | ONPE Elecciones Generales 2026 |
| Poll close | 2026-04-12T18:00:00-05:00 | 2026-04-12T23:00:00Z | ONPE Elecciones Generales 2026 |
| First meaningful results | 2026-04-12T18:00:00-05:00 | 2026-04-12T23:00:00Z | ONPE results publication notice |
| ACTIVE_RESULTS end | 2026-04-13T00:00:00-05:00 | 2026-04-13T05:00:00Z | EXPERIMENT-004A frozen event-time policy |
| LATE_COUNT end | 2026-05-15T10:04:00-05:00 | 2026-05-15T15:04:00Z | ONPE 100% acts counted notice |

DATA-001 window: `2026-04-10T00:00:00Z` → `2026-04-15T00:00:00Z`. Candidate tokens: 654; tokens with book evidence: 266.

Usability: BOTH 22; PRE_ELECTION_ONLY 0; ELECTION_NIGHT_ONLY 58; NEITHER 574.

Special anchor: **SUPPLEMENTAL_POLL_CLOSE** at 2026-04-13T18:00:00-05:00 (2026-04-13T23:00:00Z). Exceptional voting in 187 Lima tables continued until 18:00 on 13 April; JNE also extended affected overseas locations. ACTIVE_RESULTS therefore overlaps limited residual voting.
Special anchor: **ADMINISTRATIVE_RESULTS_MILESTONE** at 2026-04-22T19:53:00-05:00 (2026-04-23T00:53:00Z). The source reports completion of processing all presidential acts and is published at 19:53. This publication time is the first traceable public confirmation, not a claim about the exact internal completion second.

Sources:
- ONPE Elecciones Generales 2026: https://www.gob.pe/institucion/onpe/campa%C3%B1as/104066-elecciones-generales-2026
- ONPE results publication notice: https://www.gob.pe/institucion/onpe/noticias/1377819-resultados-de-votacion-se-publican-conforme-se-procesan-actas-electorales
- ONPE 100% acts counted notice: https://www.gob.pe/institucion/onpe/noticias/1392196-onpe-contabiliza-el-cien-por-ciento-de-actas-electorales

## Peru runoff

| Anchor | Local | UTC | Evidence |
|---|---|---|---|
| Poll open | 2026-06-07T07:00:00-05:00 | 2026-06-07T12:00:00Z | ONPE Segunda Eleccion Presidencial 2026 |
| Poll close | 2026-06-07T17:00:00-05:00 | 2026-06-07T22:00:00Z | ONPE Segunda Eleccion Presidencial 2026 |
| First meaningful results | 2026-06-07T18:12:00-05:00 | 2026-06-07T23:12:00Z | Infobae live results using ONPE updates |
| ACTIVE_RESULTS end | 2026-06-08T00:12:00-05:00 | 2026-06-08T05:12:00Z | EXPERIMENT-004A frozen event-time policy |
| LATE_COUNT end | 2026-06-29T14:50:00-05:00 | 2026-06-29T19:50:00Z | ONPE 100% acts counted notice |

DATA-001 window: `2026-06-05T00:00:00Z` → `2026-06-10T00:00:00Z`. Candidate tokens: 654; tokens with book evidence: 90.

Usability: BOTH 9; PRE_ELECTION_ONLY 1; ELECTION_NIGHT_ONLY 34; NEITHER 610.

Special anchor: **ADMINISTRATIVE_RESULTS_MILESTONE** at 2026-06-12T19:04:00-05:00 (2026-06-13T00:04:00Z). ONPE announced 100% of presidential acts processed; observed/contested acts still required resolution.

Sources:
- ONPE Segunda Eleccion Presidencial 2026: https://www.gob.pe/institucion/onpe/campa%C3%B1as/141914-segunda-eleccion-presidencial-2026
- Infobae live results using ONPE updates: https://www.infobae.com/peru/2026/06/07/resultados-onpe-en-vivo-conteo-oficial-de-votos-de-keiko-fujimori-y-roberto-sanchez-en-la-segunda-vuelta-de-elecciones-2026/
- ONPE 100% acts counted notice: https://www.gob.pe/institucion/onpe/noticias/1412568-onpe-contabiliza-el-100-de-actas-presidenciales

## Hungary parliamentary election

| Anchor | Local | UTC | Evidence |
|---|---|---|---|
| Poll open | 2026-04-12T06:00:00+02:00 | 2026-04-12T04:00:00Z | Nemzeti Valasztasi Iroda 2026 FAQ |
| Poll close | 2026-04-12T19:00:00+02:00 | 2026-04-12T17:00:00Z | Nemzeti Valasztasi Iroda 2026 FAQ |
| First meaningful results | 2026-04-12T20:18:00+02:00 | 2026-04-12T18:18:00Z | The Guardian Hungary election live |
| ACTIVE_RESULTS end | 2026-04-13T02:18:00+02:00 | 2026-04-13T00:18:00Z | EXPERIMENT-004A frozen event-time policy |
| LATE_COUNT end | 2026-04-19T00:00:00+02:00 | 2026-04-18T22:00:00Z | Nemzeti Valasztasi Iroda constituency result pages |

DATA-001 window: `2026-04-05T00:00:00Z` → `2026-04-15T00:00:00Z`. Candidate tokens: 348; tokens with book evidence: 96.

Usability: BOTH 12; PRE_ELECTION_ONLY 0; ELECTION_NIGHT_ONLY 2; NEITHER 334.

Special anchor: **RESULTS_MILESTONE** at 2026-04-12T21:23:00+02:00 (2026-04-12T19:23:00Z). At 21:23 CEST Viktor Orban publicly said the result was clear and conceded; residual counting continued afterward.

Sources:
- Nemzeti Valasztasi Iroda 2026 FAQ: https://www.valasztas.hu/gyik2026
- The Guardian Hungary election live: https://www.theguardian.com/world/live/2026/apr/12/hungary-election-latest-results-viktor-orban-peter-magyar-fidesz-tisza-russia-europe-live-news-updates
- Nemzeti Valasztasi Iroda constituency result pages: https://vtr.valasztas.hu/ogy2026/egyeni-valasztokeruletek/04/02

## DATA-001 source/version limitations

- Colombia first round, Colombia runoff and Peru runoff use PMXT V2 in the accepted corpus.
- Peru first round and Hungary cross the accepted PMXT V1-preferred / V2-only splice. V2-only market state begins only at its real first observation; no backward fill is allowed.
- Fills-only evidence never becomes book-usable. BBO changes before the first depth snapshot do not initialize a full book.
- Crossed books, empty sides, same-millisecond ambiguity, material token gaps and fill/book overlap remain reported evidence; this task does not repair or interpolate them.

## Independence for downstream validation

COL_2026 first round/runoff and PER_2026 first round/runoff are dependent event-family members, not independent elections. HUN_2026 is the third family. EXPERIMENT-004A.2 must therefore use whole-event/family holdouts rather than treating five rounds as five independent samples.

## Machine-readable artefacts

- `data/experiments/experiment_004a/event_timeline.json`
- `data/experiments/experiment_004a/regime_definitions.json`
- `data/experiments/experiment_004a/market_coverage.csv`
- `data/experiments/experiment_004a/event_window_coverage.csv`
- `data/experiments/experiment_004a/usability_matrix.csv`
- `data/experiments/experiment_004a/coverage_summary.json`
- `data/experiments/experiment_004a/reaction_diagnostics.csv`
- `data/experiments/experiment_004a/event_sources.csv`

Reaction diagnostics use exact DATA-001 depth snapshots and the already-frozen factual result clock. Thresholds refer to movement toward the token's last observed DATA-001 side; they are descriptive and are not treated as contract settlement or used to define regime boundaries.

Regime boundaries are immutable for downstream alpha work. A factual correction requires a new package version and explicit downstream-impact note.
