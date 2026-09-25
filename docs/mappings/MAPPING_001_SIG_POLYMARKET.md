# MAPPING-001 — SIG ↔ Polymarket identity crosswalk

## Scope

MAPPING-001 is the canonical identity layer between the live SIG tournament universe and the
existing read-only Polymarket market-data subsystem. It does not calculate fair value, trade,
record a new feed, or create a relationship graph.

The canonical artifact is:

```text
data/mappings/sig_polymarket_2026.json
```

The CSV and summary are deterministic derivatives:

```text
data/mappings/sig_polymarket_2026.csv
data/mappings/sig_polymarket_2026_summary.json
```

These generated live artifacts must not be hand-edited.

## Safety model

Candidate generation is deliberately non-authoritative.

String/token overlap can place Polymarket markets into a review queue, but it can never create an
`EXACT`, `NEAR`, or `DERIVED` production mapping. Direct/derived mappings require an explicit
reviewer-owned override that names the live SIG exchange and exact Polymarket market/outcome.

Without such an override the generator emits:

```text
mapping_class = NO_TRADE
status = UNRESOLVED
```

and may retain candidate Polymarket market IDs for review. This prevents similar titles from
silently becoming production token mappings.

Overrides are revalidated against the live SIG `market_id`, `exchange_id`, and outcome label on
every generation. A stale override fails visibly.

## Identity invariants

For every direct Polymarket mapping the canonical record retains:

- Polymarket market ID;
- condition ID / CID;
- event ID and slug metadata where available;
- question;
- every outcome label in Gamma order;
- every aligned CLOB token ID in Gamma order;
- the exact mapped outcome;
- the exact mapped token ID.

The mapping layer uses `PolymarketMarket.tokens()` so `outcomes[i] ↔ token_ids[i]` remains the
upstream Gamma identity. It never invents a YES/NO ordering rule.

`SAME` and `COMPLEMENT` are explicit. For a complement, the override names the actual Polymarket
outcome/token that is economically equivalent to the SIG exchange. For example, a SIG Republican
outcome mapped to a Democratic proposition uses the Polymarket `No` outcome when that is the
reviewed equivalent.

## Live generation

A live run requires the accepted read-only SIG credential and an explicit tournament context:

```bash
PREDICTIONS_CUP_SIG_READ_CREDENTIAL=... \
python -m predictions_cup.mapping.generator \
  --tournament-id <live-tournament-id> \
  --overrides data/mappings/sig_polymarket_2026_overrides.json \
  --smoke-clob \
  --require-verified
```

The generator:

1. enumerates the live tournament with `SigRestClient.iter_markets`;
2. inspects every market-node tree with `get_market_nodes`;
3. enumerates live exchanges with `iter_exchanges`;
4. discovers current Polymarket Gamma metadata;
5. resolves explicit overrides against exact Gamma outcome/token arrays;
6. leaves non-reviewed candidates unresolved/NO_TRADE;
7. validates one record per SIG exchange;
8. writes canonical JSON, derived CSV, and a derived summary;
9. optionally smoke-fetches verified mapped token books through the existing CLOB REST client.

A Gamma parse failure aborts generation rather than silently producing an incomplete identity
universe.

## Override format

Overrides are intentionally explicit and reviewable:

```json
{
  "schema_version": 1,
  "tournament_id": "LIVE_TOURNAMENT_ID",
  "records": [
    {
      "sig_market_id": "SIG_MARKET_ID",
      "sig_exchange_id": "SIG_EXCHANGE_ID",
      "sig_outcome_label": "Yes",
      "mapping_class": "EXACT",
      "mapping_direction": "SAME",
      "mapping_confidence": "1",
      "status": "VERIFIED",
      "polymarket_legs": [
        {
          "market_id": "POLYMARKET_MARKET_ID",
          "outcome": "Yes"
        }
      ],
      "semantic_notes": "Reviewed economic proposition.",
      "resolution_notes": "Reviewed settlement semantics."
    }
  ]
}
```

`NEAR` uses the same one-leg structure but must document the material semantic difference.
`DERIVED` requires at least two explicit Polymarket legs and `mapping_direction = DERIVED`.
`MODEL_ONLY` and `NO_TRADE` cannot claim direct token IDs.

## Determinism

The JSON document is canonical. Records are sorted by SIG market ID, exchange ID, and outcome.
JSON keys are sorted and CSV columns are fixed. No generation timestamp is embedded in canonical
content, so equivalent live metadata plus equivalent overrides produce byte-stable JSON/CSV.

The summary is computed from the validated document and reports SIG market/exchange counts, all
mapping-class counts, direct Polymarket market count, unique CIDs, unique CLOB token IDs,
unmapped/ambiguous records, and duplicate/conflicting mappings.

## Acceptance note

The repository code can be reviewed and tested without a credential, but the final 2026 live
crosswalk itself can only be accepted after a credentialed run against the explicit SIG tournament
and current Gamma/CLOB metadata. Historical pasted catalogues are sanity checks only and must not
be promoted into the canonical artifact.
