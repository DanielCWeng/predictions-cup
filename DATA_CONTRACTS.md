# Data Contracts

Canonical models are domain objects, not direct mirrors of remote JSON. Future adapters own transport validation and conversion into these types.

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
- wire-format timestamp strings/epoch values belong in future transport adapters and are rejected by canonical models;
- external/platform identifiers remain opaque non-blank strings;
- historical/value models are frozen and reject unknown fields;
- `OrderKind` is limited to `MARKET` and `LIMIT`: LIMIT requires `limit_price`, MARKET forbids it;
- canonical `Position.quantity` is non-negative platform holdings; signed directional exposure is not represented by `Position`;
- undocumented remote order/status values other than stable order-kind semantics remain opaque non-blank strings rather than speculative enums.
