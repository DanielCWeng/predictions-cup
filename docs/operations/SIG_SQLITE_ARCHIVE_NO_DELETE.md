# SIG operational SQLite archive: no-deletion procedure

**State, 2026-10-24:** West's 80 GiB XFS root was ~80% occupied with ~17 GiB
available. The live SIG SQLite file occupied ~39 GiB and its separate SQLite
WAL occupied ~4.4 GiB. Existing SIG research Parquet shards occupied ~1.6 GiB.
PREDICTIONS_CUP_SIG_REALTIME_RETENTION_DAYS=1 is configured, but normal SIG
capture startup deliberately defers SQLite retention pruning. Capture writes
both operational SQLite and separate raw/normalised Parquet output.

Do not interpret existing Parquet as a verified substitute for all SQLite
tables or delete, truncate, vacuum, checkpoint or move live SQLite/WAL files
without an independently verified archive and the owner's approval.

## Preconditions

1. Use an additional, approved storage volume or destination with adequate
   headroom. At 17 GiB free, DO NOT allocate a 10 GiB root swapfile or attempt
   a full 39 GiB export onto the same root disk.
2. Agree a capture maintenance window. Stop SIG capture through the
   authorised systemd pathway. The exporter checks that the capture service
   reports inactive before and throughout export. It does not stop services.
   The live maker should not be paused merely to export research data.
3. Ensure SQLite/WAL are consistent and no additional writer is active. Never
   unlink WAL/SHM files.
4. Use the project's Python environment with pyarrow. Integer, real and blob
   values are exported as UTF-8 strings; blobs use a base64: marker and nulls
   remain null. The output is an analytical export, not a lossless SQLite
   backup or sufficient evidence to drop the source.

## Bounded export in an approved 8-minute maintenance window

With SIG capture already stopped, from repository root:

    python scripts/archive_sig_sqlite_parquet.py \
      --source data/sig_realtime.sqlite3 \
      --destination /path/to/approved/large-volume/sig_sqlite_archive \
      --max-seconds 480 --batch-rows 1000 --min-free-gib 8

The script performs only read-only SQLite SELECTs. It exports rowid-bounded,
atomic ZSTD Parquet shards and a resumable manifest with original column
declarations, row counts, high-water rowids and SHA256 digests. Subsequent
maintenance windows resume unfinished tables from the manifest. It never
deletes the source or replaces existing Parquet shards.

Verify the exported shards (may take far longer than ten minutes for a full
archive, ideally on a research host):

    python scripts/archive_sig_sqlite_parquet.py \
      --source data/sig_realtime.sqlite3 \
      --destination /path/to/approved/large-volume/sig_sqlite_archive \
      --verify-only

Verification checks Parquet checksums, row counts and nonoverlapping rowid
ranges; it does NOT establish equivalence to every SQLite row. Before any
source reclamation, independently compare source and exported counts by
table/day, value fidelity, types and nullability, retention cutoffs, and raw
versus normalised families. Preserve the original until the owner approves
retention/cleanup.

## Restart and disk monitoring

Restart SIG capture through the authorised operator pathway. Confirm new
Parquet shard creation, reliable ingestion, WAL growth, free disk, CPU PSI and
maker health. Never blindly run --prune-on-startup: large SQL DELETE operations
can themselves create huge WAL files and cause production IO pressure.

No automatic pruning, external data transfer or root swap allocation is
enabled by this utility. Those are separate authorised changes.

## Swap dependency

The desired 10 GiB swap can be placed on XFS after disk headroom is expanded
or on a separately provisioned and approved volume. Swap can absorb inactive
memory pages; it will NOT provide faster CPU or SIG REST throughput. On
deployment configure swappiness, activation and reboot persistence, and
verify swap-in/out, system PSI and maker memory pressure.
