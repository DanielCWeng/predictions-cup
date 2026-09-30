# KALSHI-001 — read-only public market-data transport

This lane is intentionally incapable of order submission. It exposes only fixed public REST GET
routes for market/event metadata, order books and trades. There is no API-key/authentication
surface, generic HTTP method, order endpoint, portfolio mutation, cancel/amend path or transfer
path.

Production base URL: `https://external-api.kalshi.com/trade-api/v2`.

The transport uses decimal-string fields (for example `yes_bid_dollars`, `*_size_fp`,
`count_fp`) as `Decimal`, records local `observed_at` separately from source timestamps, and
retains `kalshi-trade-api-v2` provenance.

Retry is bounded to 429 and transient 5xx/transport failures. Deterministic CI should replace the
private GET helper with fixtures; it must not depend on live internet.

Official contract checked 2026-09-30:
- Get Markets: /markets
- Get Market: /markets/{ticker}
- Get Market Orderbook: /markets/{ticker}/orderbook
- Get Trades: /markets/trades
- Get Event: /events/{event_ticker}

Kalshi is optional external FV/research input. Availability must never by itself authorize SIG
trading.
