# FULLSTACK-002 current-main component matrix

Frozen starting `main`: `4a040a7d309df26098af5e81e96c7f1108d03117`.

This inventory is based only on code/tests on the frozen base. No active sibling branch was inspected.

| Component | Present? | Entrypoint / composition | systemd-capable? | Health output | Required config | Safe if dependency absent? | LIVE capable? | Authorization mechanism | Durable state |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CAPTURE / SIG | Yes | `python -m predictions_cup.sig.capture` | Yes | recorder/runtime health; final `--print-health` | read credential + tournament ID | Fails closed | No order writes | read credential only | operational SQLite + immutable Parquet |
| CAPTURE / Polymarket | Yes | `python -m predictions_cup.external.polymarket.recorder` | Yes | operational ingestion-health rows/logs | explicit supervised IDs in hardened mode | Fails closed / clean optional skip | public read only | none | operational SQLite + Parquet |
| MAKE | Yes | `python -m predictions_cup.maker` | Yes via FULLSTACK-002 | process + embedded OBSERVE health | maker enabled, mapping, tournament context | Fails closed | Yes in accepted code, **not exposed by FULLSTACK-002** | explicit `--live` plus BUILD-009/RISK interlocks | execution journal when LIVE |
| SHADOW | Yes | embedded in MAKE via `build_live_shadow_runtime` | Intentionally not separate | no independent process-boundary health surface | maker + shadow enabled | Yes | No order writes | none | SHADOW JSONL / optional capture mirror |
| OBSERVE | Yes | embedded in MAKE / capture | Intentionally not separate | atomic `runtime/status/observe.json` from MAKE | maker process for canonical status | Yes; not a Risk dependency | observes LIVE/SHADOW activity | none | immutable observation capture + status JSON |
| LIVE-LEARN | Yes | embedded SHADOW mirror | Intentionally not separate | internal persistence health only; no independent status daemon | shadow + live-learn enabled | Yes | scores evidence; no write authority | none | JSONL outcomes + reports |
| RISK-002 | Yes | embedded in MAKE; `scripts/risk002_control.py` offline control | Intentionally not separate | durable capital/halt state | risk profile/caps/state config | LIVE blocks if unavailable | Yes | central risk state/interlocks | SQLite/WAL |
| BUILD-009 execution | Yes | execution boundary consumed by MAKE | Through MAKE | durable lifecycle/recovery journal | execution config | LIVE blocks on unresolved state | Yes | typed execution permit | SQLite/WAL |
| Candidate providers | Yes | SHADOW frozen runtime / accepted providers | Through MAKE | SHADOW persistence only | provider-specific frozen inputs | candidate failures remain isolated | no direct writes | none | SHADOW events |

## Frozen-base conclusions

- Current main already composes SHADOW, OBSERVE, LIVE-LEARN and RISK **inside MAKE**. FULLSTACK-002 does not spawn duplicate daemons.
- The canonical FULLSTACK-002 status separates `INSTALLED`, `CONFIGURED`, `ENABLED`, `HEALTHY` and `AUTHORIZED`; they are not treated as synonyms.
- Issue #84 is **not** already fixed on the frozen base. The recovery `SigLiveSink` receives the observation emitter, while the fresh-admission constructor does not. FULLSTACK-002 therefore keeps LIVE authorization `NOT_READY` and does not modify sibling-owned execution code.
- No accepted current-main operator flatten contract exists. `cupctl flatten` returns `NOT_READY` instead of manufacturing a false implementation.
- FULLSTACK-002 status reads risk/execution SQLite in read-only mode; status aggregation does not initialize or migrate economic state.
