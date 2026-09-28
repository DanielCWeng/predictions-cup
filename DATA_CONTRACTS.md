# Data Contracts

Canonical models are domain objects, not direct mirrors of remote JSON. Adapters own transport validation and conversion into these types.

| Contract | Status | Canonical meaning / invariant | Owner ticket |
|---|---|---|---|
| TournamentContext | DEFINED | Explicit tournament identity; opaque ID with optional slug/name. | BUILD-002 |
| Market | DEFINED | Logical prediction-market question containing one or more Exchanges. | BUILD-002 |
| Exchange | DEFINED | Tradable outcome/exchange identity, distinct from Market. | BUILD-002 |
| Price | DEFINED | Timestamped observed price/probability in `[0,1]`, with source and kind. | BUILD-002 |
| OrderBook | DEFINED | Immutable timestamped bid/ask snapshot for one Exchange. | BUILD-002 |
| Trade | DEFINED | Observed economic trade with Decimal price/quantity and aware timestamp. | BUILD-002 |
| ExternalReference | DEFINED | Venue-agnostic identity for an external contract; no mapping semantics. | BUILD-002 |
| MarketMapping | DEFINED / ACCEPTED FRAMEWORK | One SIG exchange mapped to explicit Polymarket identity/token semantics, or failed closed as unresolved/NO_TRADE. | MAPPING-001 |
| FairValue | UNDEFINED / PLACEHOLDER | Later fair-value contract. | later fair-value ticket |
| Opportunity | UNDEFINED / PLACEHOLDER | Later opportunity-scanning contract. | later opportunity-scanning ticket |
| RiskDecision | UNDEFINED / PLACEHOLDER | Later risk contract. | later risk ticket |
| OrderIntent | DEFINED | Proposed MARKET/LIMIT trading action before risk/execution; never itself an Order. | BUILD-002 |
| Order | DEFINED | MARKET/LIMIT order identity/state known to the system; no submission behavior. | BUILD-002 |
| Fill | DEFINED | Financial execution fact retaining order/exchange/side/price/quantity/time. | BUILD-002 |
| Position | DEFINED | Non-negative outcome-share holdings for one Exchange; signed risk exposure is derived elsewhere. | BUILD-002 |
| DecisionRecord | DEFINED | Immutable audit/learning decision identity and reason primitive. | BUILD-002 |

Shared canonical rules:

- monetary, probability, price, and quantity values use `Decimal`; binary `float` inputs are rejected;
- canonical timestamped records require an actual timezone-aware `datetime` object and normalise it to UTC;
- wire-format timestamp strings/epoch values belong in transport adapters and are rejected by canonical models;
- external/platform identifiers remain opaque non-blank strings;
- historical/value models are frozen and reject unknown fields;
- `OrderKind` is limited to `MARKET` and `LIMIT`: LIMIT requires `limit_price`, MARKET forbids one;
- canonical `Position.quantity` is non-negative platform holdings; signed directional exposure is not represented by `Position`;
- undocumented remote order/status values other than stable order-kind semantics remain opaque non-blank strings rather than speculative enums.

## SIG transport contracts — BUILD-003

SIG endpoint payloads are validated under `predictions_cup.sig` before any canonical object is created.

Key rules:

- remote JSON numbers used for price/quantity/notional fields are decoded to `Decimal` without an intermediate binary-float conversion;
- remote ISO-8601 timestamp strings are parsed into aware UTC `datetime` values in the transport layer;
- pagination cursors remain opaque strings and are forwarded unchanged;
- organization-wide pricing contexts remain transport metadata until the caller explicitly selects a tournament;
- latest price, best bid, best ask, and spread remain distinct transport fields; canonical `Price` objects are created only for LAST_TRADE, BEST_BID, and BEST_ASK;
- price/orderbook responses currently have no server timestamp in the supplied OpenAPI schema, so canonical conversion requires an explicit observation time;
- orderbook ordering is validated against the documented bid-descending / ask-ascending contract;
- trade payload `side` values `YES` / `NO` are outcome-space metadata, not canonical BUY/SELL aggressor direction;
- history `coverage.complete` and `projectedThroughSequence` remain transport provenance;
- market-node trees are preserved as remote structure and are not yet promoted into a semantic relationship graph.

The OpenAPI `Exchange.option` field is nullable while canonical `Exchange.outcome_label` is non-null. Transport parsing preserves null; canonical conversion fails visibly rather than inventing an outcome label.

## Mapping contracts — MAPPING-001

MAPPING-001 defines accepted mapping semantics separately from `ExternalReference`.

Key rules:

- every canonical mapping record identifies exactly one SIG exchange;
- `EXACT` / `NEAR` require one explicit direct Polymarket identity and `SAME` or `COMPLEMENT` direction;
- direct Polymarket identity preserves market ID, CID, all aligned outcomes/token IDs, and the selected outcome/token pair;
- `DERIVED` requires at least two explicit component identities and does not masquerade as a direct contract;
- `MODEL_ONLY` / `NO_TRADE` cannot claim Polymarket token IDs;
- unresolved candidate similarity always fails closed to `NO_TRADE`;
- duplicate SIG exchange mappings and stale override identities fail validation;
- canonical JSON ordering/content is deterministic; CSV and summary are derived artifacts.

These contracts are accepted on `main`. The live 2026 crosswalk itself remains a separate outstanding acceptance gate under LIVE-MAPPING-GATE-001 / issue #13.


## SIG live runtime state — BUILD-006 accepted

BUILD-006 does not redefine the canonical OrderBook contract. It adds venue-runtime state around it:

- UNTRACKED_DEPTH means the exchange is known but the process deliberately makes no resident authoritative full-depth claim;
- TRACKED_UNTRUSTED means the exchange is selected for depth but depth-sensitive logic must fail closed;
- TRACKED_TRUSTED means the tracked exchange has an accepted authoritative full-depth observation; while in this state, authoritative depth owns best-bid/best-ask semantics even when a side is empty or the settled book has been cleared, so scalar fallback is forbidden;
- bulk latest-price / best-bid / best-ask / spread observations are scalar transport/runtime state and never upgrade full-depth trust; scalar BBO fallback is only for UNTRACKED_DEPTH or TRACKED_UNTRUSTED broad-monitoring state;
- Realtime trades, bookDirty, marketSettled and delivery revision metadata remain full-universe event/provenance records regardless of depth tracking;
- REST response observation time remains distinct from Realtime receive time and SIG event/source time.

Compact bulk-price observations are persisted separately from full book observations so replay cannot confuse BBO/scalar coverage with authoritative depth.


## Polymarket supervised research storage — BUILD-007 accepted

BUILD-007 separates low-volume operational state from durable high-frequency research history.

Supervised universe rules:

- the systemd path requires a non-empty externally supplied
  `PREDICTIONS_CUP_POLYMARKET_SUPERVISED_IDS`;
- identifiers may be Polymarket market IDs, condition IDs or token IDs;
- market/condition identity selects the market's aligned tokens; a token identity selects only that
  token;
- every supplied identifier must resolve against an active/non-closed Gamma market or startup
  fails closed;
- the supervised selector does not fall back to the broad election heuristic and does not silently
  add heuristic markets;
- no production mapping IDs are hard-coded in repository configuration.

High-frequency research streams are Parquet + ZSTD:

- `observations`: 1-second scalar/BBO samples;
- `book_changes`: normalized price/book changes;
- `trades`: raw at-least-once public trade deliveries; hashed rows carry deterministic
  `event_id = f(token_id, transaction_hash)`;
- `depth_snapshots`: periodic bounded-depth snapshots.

Every stream preserves exchange/source time when supplied and local process observation/sample
time separately. Price/size values remain exact decimal text in Parquet rather than binary floats.
Depth levels are nested typed structures rather than JSON text.

For public trades, the raw Parquet lane may contain reconnect/redelivery duplicates. When a
transaction hash is present, canonical replay recomputes and validates the deterministic event
identity and emits only the first `(token_id, transaction_hash)` observation across all selected
shards. Unhashed trades remain at-least-once, matching the historical SQLite partial-uniqueness
contract which de-duplicated only non-null transaction hashes.

Shards are immutable after publication. Writers stage a temporary file, write ZSTD Parquet, fsync
the file, atomically replace to the final `.parquet` name, and fsync the containing directory.
The default shard time bucket is 60 seconds with an independent row-count cap. Graceful process
shutdown flushes resident buffers; a hard process/host failure can lose only the not-yet-published
bounded in-memory shard, never mutate a previously published shard.

Operational SQLite contains only market metadata, token metadata and ingestion-health history for
fresh BUILD-007 deployments. The prior high-frequency SQLite tables remain readable as a legacy
capture format but are no longer created or written by the accepted supervised runtime.

Replay accepts either the legacy Polymarket SQLite capture or the new Parquet research directory.
For the Parquet path, operational SQLite can be supplied separately so WebSocket health/data-gap
events remain part of observable-time replay.

Periodic Gamma refresh is last-good-state preserving: after successful startup, discovery or
selection failure records a degraded Gamma status and does not replace the resident universe.
Initial discovery remains fail-closed, and post-discovery local persistence failures are not
suppressed.


## Historical replay corpus — DATA-001 (PROPOSED, under review; not accepted)

DATA-001 reuses the accepted BUILD-007 Polymarket research Parquet streams rather than defining a
parallel replay format:

- `<regime>/books/{depth_snapshots,book_changes,trades}` use the BUILD-007 schemas unchanged, with
  `source_version` and `evidence_grade` appended, and load through BUILD-005
  `load_polymarket_capture` without an adapter;
- historical `observed_at` is an explicit archive-receive-time proxy
  (`HISTORICAL_PROXY_ARCHIVE_RECEIVE_TIME`), never a claim that the project possessed the data at
  that instant; PMXT V1 rows carry no venue event time (`source_timestamp` is null);
- evidence grades are per stream: depth snapshots `BOOK_SNAPSHOT`, BBO change rows `PRICE_ONLY`,
  venue trade prints and on-chain fills `TRADE_FILL`; nothing is graded `FULL_EVENT_REPLAY`;
- on-chain fills are a separate stream keyed on `(transaction_hash, log_index, token_id)` and are
  not written as BUILD-007 `trades`, whose `(token_id, transaction_hash)` identity would collapse
  distinct fills;
- PMXT routing: V1 before `2026-04-13T19`, V2 from `2026-04-13T20:00Z`. `2026-04-13T19` uses
  deterministic V1-preferred / V2-only supplementation: V1 supplies shared book evidence; V2
  supplies genuinely V2-only market state and evidence types unavailable from V1, always from
  their actual observable time and with explicit provenance (per-row `source_version`);
- the corpus lives outside Git; its manifests, hashes, identity and quality evidence live under
  `data/manifests/historical/`.

See `docs/implementation/DATA_001_HISTORICAL_REPLAY_CORPUS.md`.

## Fee / maker-taker evidence — DATA-002 (PROPOSED, under review; not accepted)

DATA-002 is a separate, immutable auxiliary dataset of fee/refund/rebate evidence for the same five
election families, joined to DATA-001 (and its own fills) only by
`condition_id` / `token_id` / `tx_hash` / `participant_address` (= DATA-001 `maker_address`) — it
does not modify DATA-001 or define a competing fills schema:

- **fill-complete, not merely fee-transfer-complete:** every scoped fill has a row, including
  fills with zero matched fee (`fee_evidence` explains why); row counts were checked against the
  source `fills_total` and match exactly for all five families;
- `fee_evidence` distinguishes `custody_not_scanned_pre_fee_era` (absence of evidence — never
  translate to `maker`) from `no_fee_leg_observed` (scanned, genuinely zero fee) — these must not
  be conflated across the 2026-01-05 fee-introduction and 2026-04-28 V1→V2 boundaries;
- raw attribution evidence ships as columns (`order_is_match_taker_order`, `maker_address` /
  `taker_address`, `participant_address` / `counterparty_address`, individual charge/refund
  amounts, `fee_sent_by_exchange`, `fee_leg_refs`, `attribution_rules`, every ambiguity flag,
  `custody_scan`) rather than a derived `is_taker=true/false` label, so an experiment preregisters
  its own classification rule against the evidence;
- maker rebates are a separate wallet-day evidence file (`rebates/`), never used to manufacture a
  fill-level maker attribution;
- the corpus lives outside Git, on Kaggle (`polyleviathan/sig-cup-data-002-polymarket-fees`,
  private); its manifest, hashes and quality/reconciliation evidence live under
  `data/manifests/fees/`.

See `docs/implementation/DATA_002_POLYMARKET_FEES.md`.

## SIG actual-market fills / DATA-002 fee evidence — DATA-003 (PROPOSED, under review; not accepted)

DATA-003 is a separate, immutable dataset scoped to the accepted SIG↔Polymarket mapping
(`data/mappings/sig_polymarket_2026.json`), not the five broad DATA-001/DATA-002 election
families — the manifest confirms zero condition/token overlap between the two populations:

- **fill-complete:** every scoped fill has a row, including zero-fee fills (`fee_evidence`
  explains why); 132,928 fee rows for 132,928 fills;
- fee/refund/rebate attribution reuses the DATA-002 method and regime table unchanged against the
  same transaction hashes as the scoped fills, rather than redefining attribution semantics;
- a custody gap (no object for 2026-09-20 / 2026-09-21) is carried as
  `fee_evidence=custody_not_ingested` with null amounts, never imputed;
- rebates remain wallet-day evidence in a separate file, never used to manufacture a fill-level
  maker attribution;
- the corpus lives outside Git, on Kaggle (`polyleviathan/sig-cup-data-003-sig-actual-fills`,
  private); its manifest, hashes and quality evidence live under `data/manifests/fills/`.

See `docs/implementation/DATA_003_SIG_ACTUAL_FILLS.md`.
