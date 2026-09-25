# Build Ledger

Status semantics:

- `MERGED / ACCEPTED` — accepted functionality exists on `main`.
- `IN REVIEW` — requested corrections are implemented on the named branch/PR and await independent acceptance; the work is not part of `main`.
- `BLOCKED / IN REVIEW` — implementation exists only on the named branch/PR and still has a known blocker.
- `PLANNED` — no accepted implementation exists.
- An unmerged branch must never be described as implemented on `main`.

| Ticket | Status | Branch | PR | Commit | Review | Acceptance |
|---|---|---|---|---|---|---|
| BUILD-001 — Repository Foundation and Canonical Project Control | MERGED / ACCEPTED | `build/001-foundation` | #1 | head `e0695cfe61a3b5070d08e64fe872da676b32db75`; merge `88a2578e25f4ee005a01955db7d9ef619ce5be30` | final review complete; CI green | merged 24 Sep 2026 |
| BUILD-002 — Configuration, secrets and typed API/domain models | MERGED / ACCEPTED | `build/002-config-domain-models` | #2 | head `7d2dfc218e1ddc46685346b8a683bdc8a041c9ce`; merge `81cee3c0449fb5abe898df46c752ba312a5c8422` | blocker revision accepted; CI green | merged 24 Sep 2026 |
| BUILD-003 — SIG authenticated read-only REST client | MERGED / ACCEPTED | `build/003-sig-rest-client` | #4 | head `c788125df685950bf389df1adea56d166b3f9d23`; merge `9e4aef9d5bb485ca72d90a6b164e79ef06209757` | blocker revision accepted; final CI green | merged 25 Sep 2026 |
| EXPERIMENT-001A — Polymarket live data capture foundation | MERGED / ACCEPTED | `experiment/001a-polymarket-live-capture` | #5 | head `86b4a0ab6f64f00c81a1c265db3a90bff878bf00`; merge `f1783c0e5f6088f4e690ef02bc0df967e546ad57` | recorder revision accepted; final CI green | merged 25 Sep 2026 |
| BUILD-004 — SIG realtime state, REST reconciliation and replayable capture | BLOCKED / IN REVIEW | `build/004-sig-realtime-state` | #12 | current head tracked by PR | review blocker: silent order expiry can age a trusted cached book; bounded authoritative refresh fix under validation | not on `main` |

GitHub merge state and the actual contents of `main` are authoritative when documentation drifts.
