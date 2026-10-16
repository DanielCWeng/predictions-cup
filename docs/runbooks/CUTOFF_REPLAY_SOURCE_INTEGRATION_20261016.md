# SIG 2026 competition cutoff + restart replay — controlled source-only integration

**16 October 2026. Owner: Capital (#225). Operational verdict: STOP-SHIP.**
No merge, deployment, trading, cancellation, risk modification, additional venue polling, host SQLite/WAL write or authenticated LIVE permission is authorised by this record.

## Authoritative ordering and immutable evidence

| Layer | Exact source / role | Result known before this stack |
| --- | --- | --- |
| current `main` | `1c6524c8c22c43d0d2f6a1801bed38728aec7cee` | Baseline, no competition wire cutoff or default-deny startup replay |
| #244 | `07b78a84052cab7a549c3b30bce7d9f4fd6db7e8` | First current-main #202+#213 stack; independent #246 FALSIFIED clock-untrusted second-attempt classification (9 red) |
| #248 | `ff77a792947666c02a91da007e4316a5ac9753b6` | Corrected source on #244; Red Team **independent narrow SOURCE-ONLY PASS**, #250 reviewer head `a4449aedab4fa5e37504fcf2d4899fb45e583efe`, run 38948524132: 1,307 pass / 3 skip / 6 XFAIL |
| #247 | `6fec3ad66e502e59bec988e18a7995e97825f7a2` | Separate current-main default-DENY `recover_startup`; run 38946110498 1,224 pass / 3 skip. **NOT** tested on #248 at that SHA |
| **this combined draft** | branch `integration/capital-cutoff-replay-gated-20261016` from **exact #248** | Transplants #247 `recovery.py` and `test_build009_recovery.py` and adapts fake HTTP test client UTC clock to #248's enforced HTTP cutoff. Retains #248 `live.py` because its recovery nonterminal-SigApiError path already conserves UNCERTAIN risk; does NOT overwrite with older #247 handler. New head requires exact combined CI and separate independent #207 source-only verdict. |

Red Team's six original reviewer test blobs from #246 remain **unchanged**:
`2455d597b9ee7ce31ea139a2bc11fdde0280f141`,
`bf6ecc67fa405c7c4e52b7bdaf0082605f0d041b`,
`85de81dcbc4957899818da1e16ad20dfc23bdb39`,
`12622085f3555aa6a40367e0a68c2bf3232cf6fd`,
`b3ca31c88f9b93508a1b97124bcac3ca662aee04`, and
`ad17b7ed6d1ce8c1bf387143b6e81dd0343b86f9`.
They are not copied into this production source PR. Red Team must independently overlay #208/#246/#250 on this new exact source head before passing it.

## Gate / dependency matrix

| Scenario and priority | Combined source protection | Test evidence / gap |
| --- | --- | --- |
| Fresh single, batch and multi-leg | Post-REST-governor last-write decision at or after 2026-11-04 **16:59:55Z** denies write on each attempt. Previously attempted POST followed by untrusted timezone must become UNCERTAIN; never-attempted bad-clock failure may reject pre-wire. | #248 tests `test_competition_deadline.py`, `test_build009_live_sink.py`; independent #250 pass applies to #248 only. No real wire arrival-time proof. |
| Same-key 502, 503, 409, timeout, 429, cancellation | Request payload and key immutable through internal retries. Cutoff/ambiguous network error after possible send retains risk; cancellation/read-only scope exempt. | #248 test suite; independent unchanged #208. Venue 429/terminal semantics NOT independently certified. |
| Fresh duplicate logical operation | `ExecutionJournal.record_before_dispatch(fresh_only=True)` must atomically claim journal ID BEFORE any wire. Existing ID rejected and no second fresh placement. | #248 concurrent journal tests + reviewer #250 two-process same-ID tests. **Different IDs / separate reservation books are NOT globally serialised.** |
| Journalled old UNCERTAIN and failed recovery | A recovery rejection/error cannot conclusively undo the old possible execution; journal remains UNCERTAIN with reservation. | #248 `live.py` stronger `except SigApiError` path kept, #247 recovery tests. Requires authoritative order/fill state. |
| Restart PENDING/UNCERTAIN/RECONCILING | Default `placement_replay_allowed=None` blocks `recover_startup` placement replay with no order wire while preserving journal + reservation. Read-only reconciliation and documented cancellation remain reachable. | #247 tests copied and run on #248 source. This guards **only `recover_startup`, not arbitrary direct `SigLiveSink.dispatch_recovery` callers**. |
| Conditional same-key restart replay | External callback's typed `PlacementReplayAuthorization` must match op, tournament, **original** idempotency key + payload hash; issue-expiry UTC window <=30 s and journal creation age <=5 min; rechecked per recovery retry. | #247 fake HTTP 429/503/lost ACK, expired/spoofed/mismatched grants, original-key identity. **Fields are caller-supplied, not authenticated by this module; a forged grant can pass.** |
| Clock rollback across restart | No independently authenticated persisted monotonic UTC high-water or SIG server receipt time is integrated. | #208 strict XFAIL remains **STOP-SHIP**; cannot equate in-process rollback detection with this property. |
| Unknown/corrupt journal and SQLite busy | Missing/corrupt journal identity must fail closed, not authorise replay. | #247 failure-injection tests for `unresolved`. Corruption between separate local fast-path check and global reserve/release is NOT proven safe across senders. |
| #84 LIVE observations and independent operator release | Fresh admission telemetry and distinct release permissions remain outside this source-only patch. | #208 strict XFAIL, no real host evidence. |

## Cross-sender counterexample and non-waivable release prerequisites

Two independent processes can each reserve an exposure in their **own** `ExecutionReservationBook` for different logical IDs and commit different durable journal envelopes. The per-ID `UNIQUE` constraint only prevents duplicate *same-ID* writes, not simultaneous different-ID capital use. Independently, a direct caller of `SigLiveSink.dispatch_recovery` is not proven to use `recover_startup`'s newly added default-deny callback; a local synthetic grant is not cryptographic/source-authenticated authority. Thus **combined gate cannot yet be asserted unbypassable**, even if exact-head tests pass. The current 100,000 gross hard cap stays unchanged, but this patch does not establish a single cross-sender reservation lease, verified signed account epoch, or globally atomic economic dispatch.

**Mandatory before any LIVE acceptance:** independent #207 PASS on this combined exact head with untouched six reviewer blobs (no XFAIL conversion); #84 fresh-maker LIVE observation wiring; authenticated across-restart deadline clock and venue receipt semantics; authenticated replay grant issuer/provenance and single-use cross-process lease; globally conserved reservations across maker/recovery and distinct IDs; fresh signed-by-exchange inventory/gross-open-order and terminal fill proof; target West/East host and actual configuration parity; distinct owner approvals and unchanged RISK-002 limits. **No LIVE approval follows solely from a CI green status.**

### Reproduction order

1. This combined branch: `ruff check . && mypy && pytest -q tests/test_competition_deadline.py tests/test_build009_live_sink.py tests/test_build009_recovery.py tests/test_make001.py && pytest -q`.
2. Red Team independent reviewer overlay of #208/#246/#250 unchanged blobs from frozen evidence, record overlay tree SHA and run same commands.
3. Independent explicit `SOURCE-ONLY PASS/FAIL` **separate** from `LIVE STOP-SHIP`.

No venue action, deployment, cap modification or production SQLite access is part of these tests.
