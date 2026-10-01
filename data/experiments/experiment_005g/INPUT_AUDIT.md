# EXPERIMENT-005G — INPUT AUDIT

**Status:** dataset identity and mapping are bound; heavy scientific execution has not started.

## Dataset identity

- Kaggle dataset: `polyleviathan/sig-cup-data003-orderbooks`
- Kaggle status/version: `ready`, version **1**
- Planned source-hours: **2,898**
- Final acquisition status: **2,895 DONE / 3 SOURCE_MISSING**
- Physical Parquet outputs: **2,893**
- Zero-row placeholders: **2**
- Rows: **1,206,616,757**
- Output bytes: **19,117,561,321**
- Declared plan gaps: **30**

The base/worker progress manifests contain every output path, row count, byte count and SHA-256. The manifests are themselves hash-bound in `INPUT_AUDIT.json`, so the full 19 GB byte inventory is transitively bound without copying it into Git.

## DATA-003 / mapping relationship

The target manifest contains **693 condition IDs and 1,386 token IDs**. Independently extracting every Polymarket leg from the accepted 237-record SIG mapping produces the same counts and exactly the same sorted sets. Mapping SHA-256:

`9bc3d55317ace5e7e56eb0e4deaf2d36d09421b2861b7104977a96bedfc16df2`

This is the mapped Cup Polymarket universe, not a substitute corpus.

## Source generations

- **v1:** 432 hours; five-column envelope with JSON payload.
- **v2:** 1,532 planned hours; 1,529 completed.
- **ag6:** 100 hours; same sixteen-column data schema as V2, but independent collector/provenance.
- **v3:** 834 hours; microsecond receive times, sequence provenance, nested depth; later hours add witness provenance columns.

## Explicit holes

Three final source-missing hours, all in `ev11_me_nv_nd_sc`:

- 2026-06-11T04
- 2026-06-11T05
- 2026-06-11T06

Two completed filter jobs emitted no physical file because the target subset had zero rows, both in `ev09_al_ga_id_ky_or_pa_tx`:

- 2026-05-22T17
- 2026-05-22T18

These are hard continuity breaks. No rolling feature or forward target may bridge them.

## Observability

The authoritative construction rules are in `OBSERVABILITY_MODEL.json`.

For V1/V2, the repository's accepted PMXT normalizer already establishes recorder receive time as the historical observable-time proxy. AG6 uses the same V2 schema and receive-time convention. V3 must be ordered by microsecond `timestamp_received`; `sequence` is usable for exact within-hour deduplication but is not a global event clock, and physical file order is not arrival order.

## Provenance limitation

The original acquisition script was run outside the repository and its code SHA/version is not recorded in the dataset manifests. The immutable effective acquisition artifact is still bound by Kaggle dataset version 1, plan/target/progress hashes, and per-file SHA-256 values.

Accordingly:

- exploratory/canonicalization work may proceed;
- HOLDOUT remains closed;
- confirmatory publication must either recover the acquisition-code version or explicitly approve the immutable-byte/manifests provenance exception.

## Compute transport

The dataset is accessible through authenticated Kaggle dataset APIs, but a fresh Kaggle kernel with the correct server-side `dataset_sources` metadata did not receive a `/kaggle/input` mount. No substitute dataset was used. Heavy 19 GB execution is therefore an operational blocker, not a scientific result.

## Scientific status

No 005F replication has been claimed. No discovery candidate has been scored. No HOLDOUT has been read.

REAL SIG ORDERS SENT: NO
