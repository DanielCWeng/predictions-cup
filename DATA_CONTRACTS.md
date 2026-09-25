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
| MarketMapping | UNDEFINED / PLACEHOLDER | Later mapping contract. | later mapping ticket |
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
