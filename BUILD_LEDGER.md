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
| BUILD-003 — SIG authenticated read-only REST client | IN REVIEW | `build/003-sig-rest-client` | #4 | current branch head (see PR #4) | recursive `MarketNode` transport correction and regressions complete; reconciled onto current `main`; awaiting independent review | pending |
| EXPERIMENT-001A — Polymarket live data capture foundation | IN REVIEW | `experiment/001a-polymarket-live-capture` | #5 | current branch head (see PR #5) | recorder blockers corrected; integrated against corrected BUILD-003 head `986bd52a7338f790eb00b3e8e333c10e41acd7b0`; awaiting independent review | pending |

GitHub merge state and the actual contents of `main` are authoritative when documentation drifts.
