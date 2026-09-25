# HIST-DATA-001 — Historical order-book and PolyLeviathan fill-data audit

**Branch basis:** `main` commit `ab530e7d8e46d45075d958baae25f19488731f92` (BUILD-005 and EXPERIMENT-002 merged).

**Status:** PARTIAL / EVIDENCE-GRADED. The public archive families, schemas, provenance and replay limits have been verified. Large external archives and the private PolyLeviathan OCI lake have **not** been bulk-copied into this repository. The acquisition/extraction tooling in this branch is designed to produce compact, checksummed extracts outside Git.

## Executive conclusion

The prior canonical research package was directionally right about Colombia and Peru being the strongest historical microstructure families, but it was too strong to call PMXT v1/v2 exact event-replay data.

PendulumFlow's archive notes classify PMXT v1/v2 as **Snapshot Grade**: receive timestamps are millisecond-resolution and can tie, original exchange order is not recoverable within a tie, and export order within an hour is not guaranteed stable. PMXT v2 also has measured market-coverage gaps. Joseph3222's Hugging Face dataset normalizes the same PMXT source stream, so it is useful for queryability and 1-minute L2 state extraction but is not an independent recorder that can repair PMXT omissions.

Therefore, for HIST-DATA-001:

- PMXT/Joseph 2026 data is approved for **historical book-state / snapshot research**;
- it is **not** approved for queue reconstruction, exact event ordering, maker-fill simulation, cancellation inference or passive-spread backtests;
- the minimum BUILD-005 adapter in this branch intentionally consumes full-depth historical **snapshots**, not the raw ambiguous event stream;
- exact raw-event replay remains unavailable for the Colombia/Peru/Hungary periods unless a genuinely independent source with stable per-event ordering is found.

Primary public source references:

- https://archive.pendulumflow.com/
- https://archive.pendulumflow.com/formats
- https://archive.pendulumflow.com/data-notes
- https://archive.pendulumflow.com/audit/v2
- https://archive.pmxt.dev/docs/v2-data-overview
- https://huggingface.co/datasets/Joseph3222/polymarket-orderbook

## Evidence grades used here

| Classification | Meaning in HIST-DATA-001 |
|---|---|
| `FULL_BOOK_REPLAY_READY` | Stable event sequencing and adequate continuity support deterministic book reconstruction. |
| `BOOK_REPLAY_WITH_GAPS` | Stable event sequencing exists, but material coverage gaps remain. |
| `SNAPSHOT_ONLY` | Full-depth state observations are usable, but event ordering between snapshots is not faithful enough for exact replay. |
| `TRADE_ONLY` | Executed fills/trades only. |
| `PRICE_ONLY` | Price series/metadata only. |
| `UNUSABLE` | Identity, continuity or semantics fail the required gate. |

No PMXT v1/v2 instrument is promoted to `FULL_BOOK_REPLAY_READY` by this audit.

## Public order-book source audit

### PMXT v1

- Published historical window: approximately 2026-02-21T18 through 2026-04-16T05 UTC.
- Five-column JSON-heavy schema.
- Book snapshots are named `book_snapshot`.
- Trades are not recorded in v1.
- PMXT's own documentation says v1 missed roughly half of live markets because of incomplete subscription handling.
- Millisecond timestamps can tie and exact within-timestamp ordering is unrecoverable.

**Maximum grade:** `SNAPSHOT_ONLY`, and only after the exact condition/token is proved present with adequate continuity.

### PMXT v2

- PendulumFlow mirror: 2026-04-13T19 through 2026-08-09T23 UTC.
- Sixteen typed columns including `market`, `asset_id`, `event_type`, full `book` snapshots, price changes and last-trade-price events.
- The archive is sorted by `(market, asset_id, timestamp_received)`, but equal millisecond receive timestamps can tie and export order is not a faithful exchange sequence.
- PendulumFlow's independent audit measured market coverage ratio 0.8707 against on-chain fills across the pre-registered audit sample, so “all live assets” must not be treated as a completeness guarantee.

**Maximum grade for the Cup historical lane:** `SNAPSHOT_ONLY`.

### Joseph3222 Hugging Face normalization

The `Joseph3222/polymarket-orderbook` dataset contains:

- raw normalized orderbook stream derived from PMXT;
- `orderbook_1min`: one full-depth L2 state per active asset-minute;
- condition id, asset/token id, best bid/ask and full depth;
- source archive marker (`v1` / `v2`).

This is the preferred practical extraction surface for HIST-DATA-001 because the 1-minute state product explicitly avoids pretending that tied PMXT events have a recoverable micro-order.

It does **not** provide independent capture provenance relative to PMXT.

## Colombia 2026

### Identity currently established

Canonical family ids already recovered in the repository include:

- first-round event id `34582`;
- overall-winner event id `34584`;
- first-round NegRisk market id `0x9250593bd8a2156d9b3101e4b145fb68f6991ca26c906e70e6ec8dc5d3af1200`;
- Vicky Dávila market `569332`, condition `0x7c795144bf0351e82c85f844de81f29f482aaefc3b544eddeb8b7932887649e4`;
- Luis Gilberto Murillo market `569333`, condition `0xa5b21a5fba9c9da91f62cedca9d28747816a514050140708a2300cdecef87f78`;
- Claudia López market `569334`, condition `0x33b9298257eac39553c008b882ac333d2538bd493689d6710d3986d890580033`.

The exact two-token orientation for Vicky Dávila and Luis Gilberto Murillo is retained in `historical_market_mapping.csv`. Claudia's token pair is deliberately left unresolved until re-fetched from authoritative metadata.

### Coverage window

The first round (31 May 2026) and second round / overall-winner phase (21 June 2026) both sit inside PMXT v2's archive window. That establishes source-family availability, **not per-token continuity**.

### Classification

Current HIST-DATA-001 classification:

- exact Colombia instruments with a validated Joseph 1-minute extract: `SNAPSHOT_ONLY`;
- instruments not yet extracted and continuity-audited: remain gated, not promoted by family membership.

### What can run

Once a compact exact-token snapshot extract has been produced:

- HIST-LEADLAG: **yes at snapshot cadence**, not sub-millisecond/tick causality;
- HIST-RV: **yes**;
- HIST-LOO-FAMILY: **yes**, after exact family mapping is completed;
- historical maker-fill modelling: **no**;
- passive spread capture / queue survival: **no**.

## Peru 2026

The important split remains correct, but the grade is lowered.

### First round — 12 April 2026

The first round occurs before v2 begins. It falls in PMXT v1, whose live-market subscription coverage was incomplete.

Exact Peru first-round child condition/token identifiers have not been recovered and verified against v1 by this branch. Therefore the first-round gate **fails closed**.

**Classification:** `UNUSABLE` for book research until exact identity + v1 archive presence + continuity are proved.

### Runoff/final phase

The runoff/final phase lies inside PMXT v2. The overall presidential family is independently visible as a 23-outcome Polymarket event and had substantial trading activity.

However, family-level existence is not enough. Exact child condition/token extraction and per-token continuity statistics still have to be generated before any child is marked usable.

**Current classification:** gated; promote extracted exact children to `SNAPSHOT_ONLY`, never exact event replay from PMXT alone.

### Cross-institution families

Senate and Chamber families remain research candidates only. “Listed” is not equivalent to economically usable. They require exact identity, archive presence and non-trivial activity before inclusion.

## Hungary 2026

Hungary remains a v1-only election-day candidate because polling day was 12 April 2026.

### Identity defect found

The existing `market_catalogue.csv` row for market `1570162` (TISZA 70–79 seats) carried the token pair:

`66805363240264598015003237173408450891633559963269231709445243023982843050451`
`24230244897635144429856330837167890420659904925743498377004299693119043985147`

Current market metadata shows that pair belongs to the **100–109** bracket, not 70–79.

The correct 70–79 pair recovered from market metadata is:

- YES: `83055908441264540841921233882933278672268147017775925454483191794986530300065`
- NO: `20147411760853828607390454857807627352643474883320257528710509864025063086323`

Condition id remains:

`0xcb951bbdbb2bbc31803745656c98fb1cf0c0d4243c2650418e0f452ce35aae7b`.

The canonical catalogue is corrected on this branch.

### Archive gate

Exact condition identity is not enough. PMXT v1 is known to omit many live markets and this execution environment has not bulk-scanned the v1 Parquet hours for the Hungary conditions.

**Hungary book gate result: FAIL / NOT PROMOTED.**

No Hungary instrument is upgraded beyond metadata/price research by this ticket until actual v1 occurrence and continuity are measured.

## PolyLeviathan fills

Repository contracts in `DanielCWeng/polymarketwhale` establish two relevant trade lakes.

### Canonical RPC trade lake

Path:

`trades/YYYY-MM-DD.parquet`

Schema:

`timestamp, side, value_usd, price, size_shares, token_id, maker_address, taker_address, tx_hash, condition_id, log_index`

Provenance:

- Polygon RPC `OrderFilled` logs;
- canonical microstructure fill source;
- dedup key `(tx_hash, log_index)`.

Role semantics are narrower than the column names suggest:

- each row represents one signed order in the matching log;
- `maker_address` is the signed-order owner;
- `side` is maker-relative;
- the active CLOB row is identifiable when `taker_address` is the exchange contract;
- these fields are matching roles, **not token-custody or settlement roles**.

### Legacy root lake

Path:

`YYYY-MM-DD.parquet` at bucket root.

Repository documentation records 1,174 files from 2022-11-21 through 2026-02-26. It lacks `log_index`; `condition_id` is empty and joins must go via token id. Tx-hash-only dedup under-represents multi-fill NegRisk transactions.

Prefer canonical `trades/` wherever overlap exists.

### What these fills support

Defensible:

- trade intensity;
- signed order flow where side attribution is used with the documented matching semantics;
- signed notional;
- trade clustering;
- volume/flow shocks;
- price response after executions when joined to an independent price/book state;
- lead/lag between related execution streams;
- concentrated-flow response.

Not defensible from fills alone:

- resting depth;
- queue position;
- cancellation behaviour;
- quote survival;
- precise maker fill probability;
- passive spread capture;
- wallet PnL or custody inference.

### Extraction status

The PolyLeviathan repository contracts and extraction schema are verified. This environment does not have authenticated access to the project's OCI bucket, so no private OCI objects were copied here and no claim is made about the current object inventory beyond repository-documented coverage.

The extraction script committed by HIST-DATA-001 is intended to run on the PolyLeviathan host/VM with its existing Instance Principal access, producing only selected election-token extracts and manifests.

## X / Twitter discovery lane

X is useful for finding data owners, mirrors, research releases and one-off archives that do not
surface cleanly in formal documentation. HIST-DATA-001 therefore treats X as a **discovery
surface**, not as evidence of dataset contents by itself.

The 25 September 2026 search pass produced four concrete classes of lead:

- **PendulumFlow / PMXT provenance.** PendulumFlow's public archive and X presence point back to
  the PMXT corpus already audited above. This is useful provenance, but not an independent recorder.
- **Jon Becker / open trade-history corpus.** An Alter Ego thread points to
  `Jon-Becker/prediction-market-analysis` and describes roughly 36 GB of Polymarket/Kalshi
  historical **trade** data. The underlying repository also describes market metadata and trade
  history rather than L2 order-book state. Keep this as a trade/metadata lead, not book evidence.
  Discovery post: https://x.com/AlterEgo_eth/status/2040417268656644512
- **Telonex on-chain fills.** Telonex describes a research sample built from 15.3 million
  Polymarket on-chain fills. That is directly relevant to execution-flow research and useful as a
  cross-check against PolyLeviathan's fill lane, but it is still **fill-only** evidence.
  Discovery post: https://x.com/telonex/status/2022251717270573513
- **Dome.** Polymarket publicly announced `@GetDomeAPI` joining Polymarket in February 2026.
  Dome advertises historical order-book access, making it a materially more relevant candidate
  for an independent historical-book source. Exact Colombia/Peru/Hungary token availability and
  requested-window continuity have not been verified, so Dome remains a provider lead rather than
  promoted evidence.
  Discovery post: https://x.com/Polymarket/status/2024514315823509826

The X pass did **not** surface an independently verified target-period recorder with stable
per-event ordering for Colombia, Peru or Hungary. Any future X lead changes a data grade only after
it resolves to retrievable bytes or API records whose schema, token identity, requested time
window, timestamp semantics and continuity can be inspected.

## Additional historical-book providers checked

The X/web discovery pass also surfaced API vendors that deserve explicit treatment rather than being
silently ignored.

### Dome

Dome advertises historical Polymarket order-book access by token/time range and exposes
`getOrderbooks` in its SDK. Polymarket announced in February 2026 that Dome was joining
Polymarket.

This is a promising independent-source candidate, but the historical API is key/onboarding gated
and this ticket has not verified that the exact Colombia, Peru or Hungary token ids exist across
the required windows.

**Status:** `CANDIDATE_NOT_PROMOTED`.

Sources:

- https://www.domeapi.io/
- https://www.npmjs.com/package/@dome-api/sdk

### Marketlens

Marketlens documents independent WebSocket capture and a broad Polymarket archive beginning in
March 2026. However, its own standalone-event catalogue says standalone-event collection began
15 August 2026. The target Colombia, Peru and Hungary election families resolved before that
standalone collection date, so the broad "since March" statement cannot be used to certify these
specific events.

**Status for HIST-DATA-001 target elections:** `NO_VERIFIED_TARGET_COVERAGE`.

Sources:

- https://marketlens.trade/polymarket-historical-data
- https://marketlens.trade/data/events

### Resolved Markets

Resolved Markets documents full-depth historical snapshots with event timestamps, capture
timestamps and per-token sequence numbers. Its documentation also states that earliest availability
depends on when a category entered its pipeline.

No exact target condition/token presence was verified in this ticket.

**Status:** `CANDIDATE_NOT_PROMOTED`.

Source:

- https://resolvedmarkets.com/guides/polymarket-historical-orderbook-data

The presence of a provider or an advertised historical endpoint is not enough to alter a data
grade. Exact token identity, requested-window availability and continuity still have to be
demonstrated from returned records.

## BUILD-005 compatibility

BUILD-005 is merged on `main`.

The historical adapter added in this branch reads a deliberately small normalized JSONL snapshot schema and emits Polymarket `DEPTH_SNAPSHOT` `ReplayEvent` objects.

Historical snapshots generally have source/event time but not our historical local network-observation time. The adapter therefore:

- sets `source_at` from the historical snapshot timestamp;
- uses that same timestamp as a deterministic replay proxy for `observed_at`;
- returns explicit metadata stating that this is `SOURCE_TIME_PROXY`, not measured local observation time;
- never invents latency;
- never accepts the ambiguous raw PMXT event stream as an exact replay sequence.

This makes slower historical state experiments deterministic without misrepresenting the evidence.

## Immediate next experiment

The first executable historical experiment should be:

> **Colombia exact-token 1-minute snapshot lead/lag / relative-value smoke**, using only two or three verified candidate legs first.

Do not start with maker fill modelling.

The required operational step before that experiment is to run the Joseph snapshot extractor for the exact Colombia token ids, generate manifests/statistics, and pass the continuity gate.

## Remaining blockers

1. Exact Colombia overall-winner child mapping is incomplete.
2. Peru exact child ids need extraction; first-round v1 presence is unverified.
3. Hungary v1 presence/continuity is unverified and therefore fails closed.
4. No independent stable-sequence recorder has yet been found for the PMXT historical period.
5. Private PolyLeviathan OCI object inventory cannot be queried from this execution environment.
6. Dataset-level record counts, spreads, depth quantiles, gaps and checksums cannot be honestly populated until the compact extracts are materialized.

Those values must remain unavailable rather than fabricated.
