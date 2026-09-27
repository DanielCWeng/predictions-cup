# Decision 002 — Parquet for high-frequency Polymarket research storage

**Status:** ACCEPTED  
**Date:** 26 September 2026

## Context

A live broad-universe BUILD-007 soak selected 3,160 markets / 6,320 tokens. At that scale the
1-second scalar panel alone implied 546,048,000 rows/day and the 60-second depth cadence implied
9,100,800 rows/day before event deltas/trades.

The SQLite/WAL writer produced an unacceptable storage shape on the roughly 30 GiB EC2 host. At one
measured point the main SQLite file was growing at roughly 2.2 GB/hour (~53 GB/day if sustained)
and the WAL was already multi-gigabyte.

Reducing the 1-second cadence solely to accommodate SQLite would weaken the lead/lag research lane.

## Decision

Retain the research cadence and change the physical storage format.

High-frequency streams use immutable PyArrow Parquet shards with ZSTD compression:

- 1-second observations / BBO panel;
- normalized book changes;
- public trade deliveries;
- periodic bounded-depth snapshots.

Low-volume operational state remains SQLite:

- market metadata;
- token metadata;
- ingestion health.

Shard publication is bounded and crash-conscious:

```text
buffer -> temporary Parquet -> fsync file -> atomic rename -> fsync directory
```

Default time buckets are short (60 seconds) with an independent row cap. Published shards are never
reopened for append. Source/event time and local observed/sample time remain separate.

Raw Parquet trade delivery is allowed to be at-least-once. Hashed trades carry deterministic
identity and canonical replay suppresses duplicate `(token_id, transaction_hash)` observations.

## Consequences

- Legacy SQLite captures remain readable evidence but are not the current high-frequency writer.
- A hard host/process loss may lose only the bounded not-yet-published in-memory shard; it must not
  corrupt already published shards.
- Retention/compaction/object storage is a separate decision based on measured mapping-bounded
  production growth.
- BUILD-007's ARM64 live gate validated PyArrow 25.0.1, ZSTD shard production/readback and restart
  continuation on the actual EC2 host.
