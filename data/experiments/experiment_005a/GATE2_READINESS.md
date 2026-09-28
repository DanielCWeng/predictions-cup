# EXPERIMENT-005A — Gate 2 / Launch Readiness

Audited 005A HEAD: 5a79aed12c3d94f449916d37e1ac13a37731da62

Current status: engineering launch path is ready; Gate 2 remains CLOSED because no MASTER-level composite EXPERIMENT-004C freeze record exists. This document is evidence for that decision, not the gate itself.

## 004C terminal lane state

| Lane | PR | Terminal branch HEAD | Disposition | CI/readiness |
|---|---:|---|---|---|
| A | #34 | 2782277a46ad3b2147a6e85788c85f2ce77405fd | INCONCLUSIVE | CI PASS |
| B | #35 | 65bd8a81a7e4ef36e920df5e092a37b51cc00eb7 | SOFT_COMPETITIVE_EFFECT_ONLY | CI PASS |
| C | #37 | fbc8c2214969c8d9c7bd7afa7a7b20d248ecb742 | INCONCLUSIVE — no promoted cross-venue edge | CI PASS |
| D | #39 | a0c9f10eda0a38b5997af9ca70e9bf4b276d07da | NO_CONDITIONAL_EDGE | CI maintenance rerun pending at write time |

D scientific freeze remains 6adb6c2746fdba1453a5f845a515c9f56b5dab3b, empirical implementation remains 1e308d7cf16cdf17fcf8ca247000c14b758a7316, and preregistration SHA-256 remains 9eafa50476ffa3e3d793081ead48e1536c02bed5a8571ca92c981158773b2688. Post-result maintenance only suppresses frozen prose line-length lint and satisfies static typing; preregistration regeneration remained byte-identical.

## 005A A1

A1 is complete without price-response access.

- exact DATA-001 to DATA-002 fill matches: 374,891 / 374,891
- unmatched rows: 0
- duplicate role join keys: 0
- outcome disagreements: 0
- price disagreements: 0
- post-V2 clean role/fee agreement: 99.468%
- post-V2 high-confidence maker+taker coverage: 99.468%
- passive positive-fee contradiction rows: 235
- strict complement-pair attribution explanation: 119 / 235

Formal A1 disposition remains STRONG ROLE-ANNOTATION QUALITY POST-V2. No alpha inference is attached to A1.

## Launch engineering

- 005A focused committed tests: 39/39 PASS
- 005A GitHub CI at audited HEAD: PASS
- deterministic Kaggle code package: 17 files
- two independent package builds yielded identical manifest SHA-256: 506c0e7db0baa640a202048fcdb54fbd73b8cac0e79c9a5ef4faa84425cf8c45
- empirical gate is absent from the package
- preflight --empirical fails closed while the gate is absent

Resource profiling is recorded in engineering/resource_profile.json. The current runner partitions BBO reconstruction by event. Frozen-universe filtering reduces selected BBO rows materially; the largest selected Arrow footprint estimate is Colombia first round at about 713 MB plus about 273 MB of collector timestamps. Conversion to Python row dictionaries can expand this materially, so memory should be monitored on the first Kaggle run and the stage remains restartable by event if needed.

## MASTER action required

Once MASTER independently accepts/finalizes the four 004C lane handoffs, create the official data/experiments/experiment_005a/empirical_gate.json with the exact composite freeze reference required by the existing 005A preflight.

Until that exists, do not run A2/A3/A4/A5 against real future-price responses.
