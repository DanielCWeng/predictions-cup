# BUILD-005 — Deterministic Replay & Experiment Foundation

## Purpose

BUILD-005 is the measurement substrate between accepted capture and later strategy research. It
answers one narrow question reproducibly:

> Given only information observable by time T, what signal could have been generated at T, what
> executable action was available, and what happened afterward?

It does not submit orders, simulate maker fills, optimise strategy parameters, or require a live
SIG ↔ Polymarket crosswalk.

## Observable-time semantics

Every `ReplayEvent` preserves both:

- `observed_at`: when the information became observable to this system;
- `source_at`: the exchange/source timestamp when one exists.

Replay ordering uses `observed_at` only. A source timestamp can be earlier or later than the local
observation and cannot move information earlier in replay. Events that share the same observable
time are deterministically ordered by source, instrument, event type and source-table sequence,
then applied as one same-time batch before an experiment hook is invoked. This avoids inventing
causality inside one observable timestamp while keeping repeated runs stable.

For Polymarket's one-second observation panel, the row's sample `observed_at` controls when the row
becomes visible, while `state_observed_at` is retained as the quote freshness timestamp. Repeated
sampling therefore cannot make an old quote look newly fresh.

## Capture schema compatibility

The offline loaders are read-only and validate the accepted tables/columns before reading.

SIG consumes:

- `book_observations`;
- `realtime_trades`;
- `trust_transitions`.

Polymarket consumes:

- `polymarket_book_observations`;
- `polymarket_book_changes`;
- `polymarket_book_snapshots`;
- `polymarket_trades`;
- `ingestion_health`.

Decimal financial values remain `Decimal`; binary floats are rejected by the loaders. Malformed,
missing or incompatible capture schemas raise `CaptureSchemaError` rather than silently guessing.

The offline compatibility command is:

```bash
python -m predictions_cup.replay \
  --sig-db path/to/sig.sqlite3 \
  --polymarket-db path/to/polymarket.sqlite3
```

It performs no network access and reports records loaded, observed time span, instruments, trusted
SIG book observations, external quote observations and explicit data-gap events.

### Bounded real-capture selection

Large capture databases are not materialized wholesale when an experiment needs only a small
slice. `CaptureSelection` pushes the following predicates into SQLite before rows are converted to
`ReplayEvent` objects:

- optional observable-time `start_at`;
- optional observable-time `end_at`;
- explicit SIG exchange IDs;
- explicit Polymarket token IDs.

The time interval is half-open: `[start_at, end_at)`. Every accepted source table is filtered on
the timestamp that controls replay visibility: SIG `rest_observed_at` / `observed_at`,
Polymarket `observed_at`, and ingestion-health `recorded_at`. Global SIG trust transitions and
Polymarket feed-health rows remain in a selected slice because they can invalidate selected
instruments.

For example:

```python
selection = CaptureSelection(
    start_at=start,
    end_at=end,
    sig_exchange_ids=("36",),
    polymarket_token_ids=("token-id",),
)
sig_events = load_sig_capture(sig_path, selection=selection)
poly_events = load_polymarket_capture(poly_path, selection=selection)
```

The offline inspector accepts the same bounds without creating replay events:

```bash
python -m predictions_cup.replay \
  --sig-db path/to/sig.sqlite3 \
  --polymarket-db path/to/polymarket.sqlite3 \
  --start-at 2026-09-25T12:00:00Z \
  --end-at 2026-09-25T12:10:00Z \
  --sig-exchange-id 36 \
  --polymarket-token-id token-id
```

Its counts, time span and instrument discovery are SQL aggregates / distinct queries rather than a
full `ReplayEvent` materialization. Only the relatively sparse ingestion-health payloads are read
to classify disconnect gaps.

Window starts are deliberately fail-closed. A bounded slice does not inspect or inherit a quote,
trust transition or feed-health state that occurred before `start_at`. If an experiment needs
warm state, the caller must request an explicit pre-roll by moving `start_at` earlier; BUILD-005
does not invent prior state.

## Replay state and validity

Replay reconstructs per-instrument state sufficient for first experiments:

- best bid / best ask;
- bounded depth when a captured snapshot contains it;
- last trade;
- quote observation time;
- SIG trusted/untrusted state;
- external book validity / feed availability.

SIG trust comes from the accepted BUILD-004 transition stream. `UNTRUSTED_*` and `RECONCILING`
transitions are non-executable; `TRUSTED*` transitions restore trust. An experiment cannot use an
untrusted SIG quote as an executable entry or target.

External feed silence is not treated as trustworthy forever. Every experiment configures an
external freshness bound, evaluated against the quote's actual state-observation timestamp.
Optional SIG freshness can also be configured; otherwise BUILD-004's authoritative refresh/trust
mechanism is the primary SIG freshness gate.

Invalid outcomes are explicit. Current reason codes include no executable start, no valid future
quote, SIG untrusted/stale, external stale, data gap, missing pair and dataset end.

## Executable markouts

Default horizons are:

```text
1s
5s
30s
1m
5m
```

They are configurable per experiment.

For a bullish / BUY-YES crossing decision:

```text
entry = decision-time ask
future executable exit = future bid
gross markout = future bid - entry ask
```

For the economically opposite / SELL-YES crossing decision:

```text
entry = decision-time bid
future executable cover = future ask
gross markout = entry bid - future ask
```

Midpoint markout is retained as an optional diagnostic, but executable markout is the primary
economic target. BUILD-005 does not model maker fills.

Target evaluation is lookahead-safe. If a requested horizon falls between two replay events, the
state at the horizon is the latest state observed at or before that horizon. The next later event is
not borrowed backward. If the target extends beyond the dataset, the observation is invalid with
`dataset_end`.

## Experiment API

`ExperimentSpec` contains:

- id and description;
- required inputs;
- parameters;
- explicit `InstrumentPair` entries;
- feature builder;
- signal rule;
- target horizons;
- freshness requirements;
- optional per-share estimated cost.

Instrument pairing may come from test fixtures, manual experiment configuration, or future
accepted MAPPING-001 artefacts. There is no title-based pairing and no dependency on issue #13
being closed.

`ExperimentRunner` is single-pass. Decisions are created only after the current observable-time
batch has advanced state. Pending targets are resolved before a later event is applied if their
horizon lies between event times, or after a same-time event batch when the target timestamp
matches exactly. This is the core no-lookahead guarantee.

Experiment output records the decision time, instrument, features, signal/direction, executable
entry, horizon, future executable price, gross/midpoint markout, optional cost, net markout and
validity reason. `serialize_observations` produces deterministic sorted JSON bytes for regression
and evidence capture.

## Chronological split discipline

`ChronologicalBoundaries` and `split_observations` provide explicit train / development / holdout
boundaries. Rows are sorted chronologically and never random-shuffled. BUILD-005 does not tune
parameters; later work must keep holdout data outside parameter selection.

## Basic evaluation

Dependency-light summaries cover valid opportunities only:

- count;
- mean / median executable markout;
- hit rate;
- total gross markout;
- total net markout when costs exist for every valid row;
- simple p25 / p75 quantiles.

A helper groups by instrument, direction and horizon. No Sharpe ratio is manufactured from
potentially overlapping opportunities.

## Acceptance proof

The synthetic regression encodes:

```text
external quote changes
→ external event becomes observable
→ SIG remains unchanged
→ SIG later reprices
```

It proves that source timestamps cannot create lookahead, executable entry/exit use the spread,
future targets use only state observable by the horizon, stale/untrusted state is invalid, and
repeated runs serialize identically. It is a framework proof only, not evidence of trading edge.

## Scope boundary

Still not implemented here:

- production fair value;
- strategy optimisation;
- live signals or trading;
- order submission/cancellation;
- maker/queue simulation;
- risk or portfolio engines;
- P&L accounting;
- global probability solvers;
- LOO-FAMILY;
- opponent simulation;
- complex ML;
- historical-election download pipelines.
