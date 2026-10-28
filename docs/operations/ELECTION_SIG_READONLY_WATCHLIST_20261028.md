# Election-night read-only SIG watchlist (28 October 2026)

This is a **single-process, read-only account and executable-book survey** for the election strategy. It is not a forecasting model, does not route orders, does not alter the maker's configuration and makes no claim of positive EV.

## Run on an authorised machine

Requires Python environment with `predictions-cup` installed and the normal `PREDICTIONS_CUP_SIG_READ_CREDENTIAL`, `PREDICTIONS_CUP_TOURNAMENT_ID` and `PREDICTIONS_CUP_TOURNAMENT_SLUG` available. For security, do not paste or write credentials into this command:

```bash
python scripts/ops/sig_election_snapshot.py --race Maine --race Kansas
```

By default, this selects only **VERIFIED + EXACT + SAME** 2026 market mappings:
- Maine Senate: SIG exchanges **959** (Democratic), **960** (Republican). **Important:** Maine's 2026 U.S. Senate contest uses ranked-choice voting because declared write-in candidates qualified. Its election-night first-choice returns are not conclusive if nobody receives a majority. The readout flags this caveat per exchange. Source: https://www.maine.gov/sos/elections-voting/upcoming/ranked-choice-voting-tabs and the 2026 SOS Candidate Guide.
- Kansas Senate: **1045** (Democratic).
- Iowa/Texas: **DERIVED** (not included in the default snapshot).
- Alaska Senate **NO_TRADE** (excluded even with `--include-derived`).
- Ohio Senate absent from the current 237-row crosswalk (not invented).

To **observe**, not authorize trading in, derived markets:

```bash
python scripts/ops/sig_election_snapshot.py --race Iowa --race Texas --include-derived
```

The command prints one JSON document to standard output with the raw account cash balance, reported inventory in the selected exchanges, open-order counts and the actual SIG price/size ladders returned by the participant GET endpoints. It includes per-book request start/end timestamps and reports mapping exclusions. It deliberately does not compute `available_cash`, derived prices, or executable EV without independent evidence. No disk persistence is necessary. Running it on a live machine is a **separate operational permission** and has not been done by the PR author.

### What to do with the output

1. Independently validate a joint election probability scenario and settlement rules. The existing shadow election-nowcast module does *not* provide a validated calibrated production estimate.
2. Build a `TradePlan` JSON from **size-weighted executable depth**, not a top-of-book midpoint; the spread, fees, risk and position claims must still be checked. Do not infer incremental buy-side power directly from the account balance: existing venue holds can already be included.
3. Score alternative plans with `python scripts/ops/election_terminal_score.py plan.json` from PR #368, now on `main`. Its leaderboard target is a scenario to beat, **not** a guarantee of first place.
4. Treat the score as research. Actual venue submissions still require a separate authorized execution path, fresh reconciled account, reliable election evidence and SIG central risk approval.

The SIG survey uses existing `SigRestClient` GET methods only; no write client is instantiated. It caps one sample to 20 markets, polls sequentially with 750 ms between book reads and reports account/books as **non-atomic**; it does not run as a 24/7 background monitor. Under concurrent trading, fills between account and book requests can invalidate the apparent capacity; a new authoritative reconciliation is needed at any actual execution decision.

## Release boundary

GitHub CI validates this command with synthetic account/book mocks. It has not been executed against a real SIG account or on West, and has not been deployed. Real-source result adapters and calibrated forecasting probabilities remain unproven. **Do not point a live trading process at the synthetic test scenarios.**
