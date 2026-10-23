
# Isolated local-private research sink v0 — **DRAFT / DEFAULT OFF**

Research-only code from Data/Ops #240; no venue clients, network calls, subscriptions, service wiring, AWS/S3/Kaggle uploads, account/order access, data copy or launch. Import/construction without a release makes **no directory**. A caller must explicitly invoke a separately independently verified operator permit; the included `verify` callback is an **interface seam, not** a signature authority. Synthetic tests use `lambda _: True` **only on invented records**. A real issuer packet is **NOT authenticated** by supplying its own producer/run SHA or original-payload SHA.

**Scope coordination:** Alpha2 #206 produces trusted-to-be-checked original decoded source events; Data/Ops owns ONLY the local append-only sink. Do not wire to the maker or second websocket consumer. Sink `append(row, original_payload)` consumes an already permissioned immutable raw payload as transient bounded bytes (<=65,536), hashes but **does not store raw bytes**, and stores an exact allowlist of original-source metadata and full ladders. No data path, credential, private ID or actual user information is committed. Only four frozen SIG 2026 EXACT exchange IDs **912/913/959/960**, mapped to m223/m224/m270/m271. No claim that current West capture supplies them; West's 18 Oct metadata showed SIG tracked full L2 **zero** and PM seed continuity **UNKNOWN**. Do not invent data to satisfy these fields.

## Interface contract

Common required fields: `kind`, `exchange_id`, `market_id`, `producer_git_sha`, bounded `producer_run_id`, `publisher_utc`, `received_utc`, `clock_uncertainty_ms`, `connection_epoch`. Both clocks UTC; publisher cannot be future of receive with uncertainty nor >5s earlier at receipt (a conservative research rejection assumption, **NOT an issuer SLA**). Values must be typed integers/strict ASCII IDs; no extra/free-text fields permitted.

- `SIG_BOOK` / `SIG_TOPIC_REV`: `asof_sequence`, `native_asof_utc`, `revision`, `previous_revision`, `source_sequence_from`, `source_sequence_through`. SIG_BOOK requires two sorted uncrossed ladders `bids` and `asks`, each <=256 `[[price-string, size-string], ...]` levels. SIG_TOPIC_REV carries **no** book; must accompany non-book topic revisions to avoid pretending filtered books preserve continuity. Adjacent source intervals and revision chain, same connection epoch, ascending native sequence, publisher uncertainty-separated chronology mandatory. No REST/scalar fallback.
- `PM_SEED` / `PM_CHANGE`: `seed_epoch`, `local_ordinal`, `drop_total`, `seed_complete`, both full reconstructed ladders. PM_SEED requires complete `seed_epoch=connection_epoch`, ordinal zero; PM_CHANGE requires same epoch, next contiguous LOCAL ordinal and ascending publisher uncertainty interval, zero drops, complete book. `local_ordinal` is **NOT** Polymarket venue-global WS sequence. Reconnect/seed reset **aborts the run**; no silent restart or event throttling.
- A gap or explicit health warning from Alpha2's producer is **not** an accepted event: append with unsupported kind/fields to force irreversible rejection or call `abort`. Source independence, completeness, signer and issuer authenticity must still be separately evidenced. The sink cannot deduce missing connection health from intermittent accepted rows. Source adapter must forward all continuity-relevant batches or STOP; never manufacture ordinals.

Append writes canonical JSONL with **SHA256-chain** over each allowed row and `original_sha256` computed over *caller-supplied* original payload, plus final sealed `receipt.json` with streaming file SHA256, rows, bytes, recorded abort reason, source SHA and purge deadline. `verify_sealed_archive` scans source bounded line by bounded line and rejects corruption/truncation; only a local integrity receipt, **not** actual issuer attestation. Storage is created exclusively at an absolute, previously non-existing directory under an operator-private non-group/world-writable parent; `umask 077`, directory `0700`, files `0600`, `O_EXCL|O_NOFOLLOW`, single synchronous writer with **zero buffered queue** and per-event file `fsync`. No outbound transfer. Named operator must privately verify disk encryption requirements, host/maker noninterference and filesystem back-up/sync settings: chmod alone does not prove encryption.

Configuration has explicit finite `max_rows<=2,000,000`, `max_bytes<=2GiB`, per-original hash input <=64KiB, per-stored row <=64KiB, per-ladder <=256 price levels, minimum free bytes and inodes checked before every append, 24h elapsed runtime and independent `expires_utc` checked on append/screen; any bad source, clocks, duplicate consecutive original hash, bounds or disk capacity => **irreversible abort and receipt**. An operator supplies a purge deadline after expiry and at most 72h later (or sooner if venue terms require). An explicit `purge_after_deadline` method is provided for supervised use, **not** an automatic timer, lifecycle, secure overwrite or compliance certificate; separate real purge controller/receipt remains an operational gate. Neither owner YES nor synthetic `verify=True` grants capture authorisation.

## Typed economics, never real PnL

`reference_economic_rows(ex)` returns four immutable `ReferenceEconomicRow`s for n=1/5/10/20. It requires PM **changed seeded** book strictly before a SIG native full-book by UTC publisher/receipt including uncertainty and max-5s research freshness, then computes **both venues** conservative per-price-level `ROUND_DOWN` whole-share YES bid/ask VWAP. `YES reference = PM YES bid VWAP − SIG YES ask VWAP`; `NO reference = SIG YES bid VWAP − PM YES ask VWAP` for the **same** SIG exchange; different-party YES is never inverted as NO. All insufficient depth or noncausal evidence => NULL. **At-arrival 250ms/1s/2s, genuine resting persistence, native attempted/nonfilled/fills, fees and closed cash are ALWAYS NULL**; reference gap is not executable net, paper admission or profit. A future separate authentic arrival/book/fee/settlement analysis requires additional permission and independent source attestation; do not attach invented numeric fees or realized cash to this stream.

## Synthetic bounded resource evidence and source decision

Targeted invented 10,000 sequential SIG entries with one bid/ask level: `10,000 rows`, `6,744,466 bytes` (about 674 B/record), `~1,575 rows/s` on an isolated test container, `~33 KiB tracemalloc peak` within test loop, no unbounded per-event list. Every write includes synchronous flush; the benchmark does **not** certify West RSS, fsync latency, storage I/O, kernel page cache, maker isolation or 24h performance. At 674 B average a 2GiB cap could fit ~3.18m rows, but configured **2m row ceiling** arrives first; ~23.15 accepted rows/sec average for 24h. Real full native 256-level books can be much larger; **capacity and source rate must be measured on isolated synthetic representative bursts before live approval**. PR #318's 20k/8MiB limits are independent and must not be used to advertise a full-rate continuous 24h tape. No silent thinning that breaks SIG revision/PM seed continuity. A disk abort is not a restart licence.

**Tests:** default off, private permissions, seed/orphan/reconnect, SIG sequence gaps/native-source replay, PM received/clock and PII field rejection, integer-lot/NO-VWAP reference-only NULL semantics, duplicate payload, corrupt/truncated line detection, finite-byte/row/inode guards, expiry, explicit purge and 10k-row burst memory/capacity. **Independent source proof and code review remain pending.**

**Release gate:** Daniel privately attests SIG private internal source use (owner-attested conditional) and **PM original 24h on-host retention basis (UNKNOWN)**, expiry/deletion, current four-market SIG native L2+PM seeded source availability, shared SIG 100-read/min quota and maker/CPU/RSS/disk/NTP noninterference (#199 reported low memory/no swap and #220 perf FAIL). Require targeted #207 new-code security/clock review and separate explicit operator YES for any actual observer run. `R1 COPIED 0/4`, `R2 ACQUIRED 0`, no Kaggle. #207 **LIVE STOP-SHIP** absolute.

## PR #319 wire-compatible local sink ABI (18 October addendum)

**Source-only, no publication authority:** Data/Ops reviewed Alpha2 DRAFT PR #319 at exact
`4bcb6b86a4fc4db8ca3780e1ee68588c7a5a5f5c`; its `SourceBridge(emit=...)`
callback emits **one** JSON-compatible `dict[str, object]`, not original WS bytes.
The v1 emitter schema is `alpha2-source-bridge-v1`. The narrow sink callback is
`PrivateStreamingSink.append_bridge(record)`; the separate pre-existing
`append(row, original_payload)` is retained only for its synthetic regression
fixtures. **Never mix ABIs within one run**. The callback is synchronous and
propagates every rejection; source bridge must poison itself on sink exceptions.
No runtime hook or bridge/producer import exists in this sink and no automatic
capturing, network read, service start or scheduler is installed.

| Bridge event | Required continuity and preserved proof boundary |
|---|---|
| SIG `venue=SIG,kind=BATCH` | Persist **every topic delivery** for four frozen exchange IDs, even when `full_books=[]`. Require matching market/tournament IDs, monotone `revision/previous_revision`, valid `source_sequence_from/through`, `connection_epoch`, UTC receive and uncertainty, optional native `asof_sequence/asof_at` plus validated full `bids/asks`. First source event is marked `gap_or_unanchored=true` and remains **unanchored**, not admission. Later gap/reconnect or native clock regression irreversibly stops. Delivery ranges are NOT falsely treated as exchange-global book sequence or complete issuer authentication. |
| PM `venue=PM,kind=REST_SEED_COMPLETE` | Verify one of four frozen **PM token IDs**, real local `connection_epoch`, local `ws_local_ordinal=0`, complete sorted full book and immutable producer SHA; save one seed per token per epoch. This is producer-reported seed completeness, not original REST issuer attestation. |
| PM `venue=PM,kind=WS_EVENT` | Persist **every consecutive local ordinal**, including unchanged/no-target-price messages (`affected_tokens=[]`, `full_books={}`); no rewriting a local ordinal into an exchange-global WS sequence. Only allow complete reconstructed books for affected, previously seeded frozen tokens. Gap, unknown reconstruction, uninitialized token or abstain requires irreversible stop, never thinning or a silently resumed connection. |
| PM `venue=PM,kind=DISCONNECT` | Persist exact terminal disconnection marker, then seal and raise a fatal error; a new approved run/seed is required. |

**Source identities:** `raw_frame_sha256` or `raw_response_sha256` are either
`null` (the usual original decoded source where untouched wire bytes do not
exist) or an **unverified** 64-character digest. `wire_bytes_verified` must be
`false`; `issuer_authenticated`, `source_admitted` and `paper_admitted` must
all be `false`. The separately labelled **`local_canonical_sha256`** is
computed over normalized record metadata for corruption checks; **it is NOT
an original issuer or wire hash**. No replacement digest is inserted into a
missing wire-hash field. The sink does not synthesize missing PM publisher UTC,
issuer fee/order/cash or a 250ms still-resting book.

**Privacy and bounds:** Strictly no arbitrary/extra JSON keys, tokens outside
the four frozen mappings, emails, credentials, order/account fields, network
endpoint, payload/string freeform, or raw venue bytes on disk. Allowlisted
SIG full depth is limited to 256 price levels per side, 16 books per topic,
64 KiB per canonical stored row; larger authentic bursts **abort**, never
silently truncate (PR319 source bridge itself allows up to 4,096 ladder
levels, so **full-rate storage feasibility is not established**). Sink retains
only O(four markets/tokens) connection/sequence counters with no write queue.
Every row uses a canonical local digest and SHA256-linked JSONL; per-record
`fsync` and prewrite `statvfs` free-byte/inode guard must pass. Existing
owner permit requires explicit external verifier, new nonpublic 0700 directory,
0600 files, 24h runtime and expiry, `<=2,000,000` rows, `<=2 GiB` bytes and
finite purge deadline `<=72h` from expiry. The explicit purge method is
**NOT** automatic secure deletion, a retention scheduler or approval to persist.
Economic diagnostic rows retain four n=1/5/10/20 frozen sizes, but the PR319
bridge lacks verified PM publisher times and original arrival/fees/cash;
therefore they remain **NULL / SOURCE_NOT_ADMITTED / PAPER_NOT_ADMITTED**.

**Test-only evidence:** All previous 18 synthetic tests remain and the PR319-
shaped tests cover nullable wire hashes, exact field allowlist, ordinary nonbook
SIG delivery, unchanged PM ordinals, gap/seed/dropped-book/clock/PII aborts,
`DISCONNECT` terminal-marker sealing, mixed ABI denial, sink disk exception,
corrupt/truncated files, and a bounded 4,000-record synthetic burst. A green
local test is neither normal repository CI nor a measured West-host perf pass.

**Operational STOP:** West #199 has 0 tracked native SIG full L2, uncertain
PM seed/callback chain and shared API headroom, limited RAM/no swap and #220
perf FAIL. Prospective PM original data 24h on-host retention basis is still
UNKNOWN. GitHub publishing was safety-blocked; do not bypass it with other
GitHub tooling, another person/identity or Alpha2 PR #319. No merge, launch,
upload, service change, venue/source read or trade. #207 LIVE STOP-SHIP.

### PM receipt-interval chronology (Red Team #207/6254170919)

In the PR319 v1 ABI the sink independently enforces STRICT receipt chronology
for **all PM tokens combined in one connection epoch**, including each
`REST_SEED_COMPLETE`, changing/unchanged `WS_EVENT`, and `DISCONNECT`.
For consecutive PM events A then B require
`A.received_at + A.uncertainty_ms < B.received_at - B.uncertainty_ms`.
Equal timestamps, overlapping uncertainty windows, stale/reordered receipts,
and epoch mismatch fail closed with
`PM_RECEIPT_NONMONOTONIC_OR_AMBIGUOUS` **before the row is written**.
The terminal `DISCONNECT` row has no uncertainty field in v1, so it uses
the last durable PM event's declared uncertainty from that pinned epoch
(PR319's one-uncertainty-per-bridge convention); never silently treat it as
zero. The PM interval state advances **only after write+fsync succeeds**,
not on validation failure. SIG delivery batches retain their independent
non-decreasing global receipt check and are allowed to have equal receive
timestamps. This is synthetic contract checking, not issuer clock attestation.
