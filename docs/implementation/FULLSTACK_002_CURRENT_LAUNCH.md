# FULLSTACK-002 component matrix

Frozen starting `main`: `4a040a7d309df26098af5e81e96c7f1108d03117`

| Component | Present on frozen main | Launch composition | Write authority | Readiness |
| --- | --- | --- | --- | --- |
| BUILD-009 execution boundary | Yes | Reused through MAKE | Not exposed by FULLSTACK-002 | NOT_READY for LIVE |
| MAKE-001 | Yes | `predictions-cup-maker.service` | Forced SHADOW only | READY for SHADOW when configured |
| CAPTURE-001 SIG | Yes | `predictions-cup-sig-capture.service` | Read-only | READY when credential/tournament configured |
| CAPTURE Polymarket | Yes | Optional supervised service | Public read-only | READY when explicit universe configured |
| SHADOW-002 | Yes | In-process with MAKE | None | READY when enabled |
| OBSERVE-001 | Yes | In-process with MAKE | None | READY; health file monitored |
| LIVE-LEARN-001 | Yes | SHADOW mirror in-process with MAKE | None | READY when enabled |
| RISK-002 | Yes | In-process with MAKE | Capital-control/halt state only | READY when configured; no LIVE authorization |
| Global halt control | Yes | `cupctl halt` delegates to RISK-002 | Durable halt after MAKE stop | READY |
| Flatten control | No accepted operator contract | `cupctl flatten` | None | NOT_READY |
| Atomic `latest.json` | Added here | Status timer | Status file only | READY |
| Durable failure alerts | Added here | `OnFailure` + transition poller | Append-only JSONL | READY |
| LIVE service/drop-in | Deliberately absent | None | None | NOT_READY |
| Intended-host acceptance | External runtime evidence required | Not run in this lane | N/A | NOT_RUN |
| Real-order rehearsal | External runtime evidence required | Not run in this lane | N/A | NOT_RUN |

## Frozen-base blocker carried forward

Issue #84 remains reproducible on the frozen starting SHA: the fresh-admission `SigLiveSink`
constructor in `maker/service.py` lacks the observation emitter supplied to the recovery sink.
FULLSTACK-002 therefore reports LIVE authorization as false and does not create a live launch path.

## Issues targeted for grading

- #87: service failure/StartLimit/global-halt visibility now has durable local alert evidence and
  systemd `OnFailure` wiring.
- #80: the lane does not claim the missing real-order rehearsal is fixed. It replaces ambiguous
  placeholders with explicit machine-readable `NOT_RUN`/simulation-only evidence and keeps LIVE
  unavailable until real host/order evidence exists.
