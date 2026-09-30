# R3-FV-001 — Implementation Specification

## Decision

R3-FV-001 does **not** supply a production directional fair-value module for the 2026 SIG Cup.

No TRAIN/DEV structural, direct+struct, calibrated, or historical machine-speed lead-lag method passed its frozen gate. FINAL remained unopened. MAKE must therefore not quote from an R3 structural point estimate and must not treat the isolated favorable Senate timing observations as alpha.

The accepted direct Polymarket mapping remains the R3 baseline/reference input. The R2.5 semantic graph, DATA-004 structural universe, and partial-identification machinery remain useful as diagnostics and live research infrastructure.

## Runtime contract

If the structural research adapter is exposed to MAKE, use a result object with explicit identification and quality fields rather than a naked probability:

```python
from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True)
class StructuralFVResult:
    sig_market_id: str
    asof_time: datetime
    fair_value: float | None
    lower_bound: float | None
    upper_bound: float | None
    confidence: float | None
    method_id: str
    method_class: str
    source_count: int
    effective_source_count: float | None
    freshness_seconds: float | None
    identified: bool
    assumptions: tuple[str, ...]
    provenance: tuple[str, ...]
    quality_flags: tuple[str, ...]
```

## Production behaviour for launch

For R3 in launch production:

- `method_id = "R3-B00-DIRECT"` for accepted direct/reference FV.
- No R3 structural `fair_value` may override direct FV.
- Structural bounds may be emitted for monitoring only.
- Add quality flag `R3_NO_CONFIRMED_STRUCT_ALPHA`.
- If a hard semantic relation is not independently verified, add `SEMANTICALLY_SOFT`; do not convert it into an executable constraint.
- If a structural source is stale, surface age; do not silently freshness-weight it into a production FV.
- Do not use PRED-006 hazard as a directional adjustment. It remains update-likelihood evidence only.
- Do not add flow features to R3 as a rescue layer pre-launch.

## Research-only structural diagnostics

The following may be computed off the live capture path without affecting quotes:

1. hard-identity / partial-identification feasibility;
2. coherent QP and KL projections;
3. LP lower/upper bounds;
4. source freshness and coverage;
5. source-shock timestamps;
6. direct-target response timestamps;
7. live lead/lag response counts at 1s/2s/5s/10s/30s/300s;
8. semantic mismatch flags;
9. structural residuals clearly labelled `RESEARCH_ONLY`.

The monitor must keep source observations and direct target observations separately timestamped. A source move must precede a target response; do not infer leadership from same-block or repeated-state observations.

## What MAY would look like for a new live programme

A future live R3 successor may be opened only with a new frozen protocol. A sensible first live test is:

`observable DATA-004 / mapped Polymarket order-book aggregate shock -> later SIG quote/trade change`

That test should use actual live books rather than sparse historical fill coincidences. It should freeze:

- exact source family and target;
- observation timestamp definition;
- minimum source move;
- response horizon(s);
- latency budget;
- duplicate-shock handling;
- stale-source rule;
- baseline;
- minimum event/day support;
- adverse-selection / executable markout metric.

The untouched historical R3 FINAL slice must not be opened merely to tune such a live hypothesis.

## Interfaces to existing systems

### Mapping / semantic graph

Read canonical accepted mapping plus R2.5 relationship metadata. Preserve relationship class and mathematical class in provenance.

### Capture

Live source/target monitoring should consume block/timestamped capture records. Do not make the research adapter responsible for order placement or risk.

### MAKE

MAKE receives either:

- accepted direct FV, or
- no structural override.

If a later separately confirmed structural model exists, add it as a new pluggable FV provider rather than changing core execution/risk machinery.

### Risk

R3 contributes no new sizing rule. Existing MAKE/RISK controls remain authoritative.

## Explicit non-uses

Do not deploy:

- Georgia joint QP/KL/MaxEnt point FV;
- Senate x House coherent point FV;
- House seat-count MaxEnt point FV;
- Senate T50/T51 point FV;
- raw structural-residual error correction;
- TRAIN-affine calibrated seat ECM;
- historical scalar seat lead-lag;
- historical CLR distribution-shape lead-lag;
- isolated favorable Senate timing rows;
- flow or PRED-006 hazard as a structural directional rescue.

## Scientific boundary

The historical corpus is fill-based. A negative historical machine-speed result can mean insufficient synchronous target observations as well as no economic lead. Accordingly, R3 closes the historical alpha claim but does **not** prove that a live order-book lead cannot exist.

That distinction is why the semantic graph and live monitoring plumbing are retained while the point-FV models are rejected.
