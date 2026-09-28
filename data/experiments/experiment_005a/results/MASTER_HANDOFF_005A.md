# MASTER Handoff — EXPERIMENT-005A

## Status

**COMPLETE / CANONICAL RESULT PACKAGE VERIFIED**

Programme disposition:

**NARROW SAME-FAMILY 5s EFFECT ONLY / NO BROAD ROLE-AWARE EDGE**

No executable-alpha, P&L, threshold, or live-order claim is made.

## Canonical provenance

- DATA-002 merge base: `69cb1924751515a495bf99556819147ad090d67d`
- canonical EXPERIMENT-004C programme freeze: `ba938bedcf63f562be8b26c9502e828391123867`
- observed empirical implementation: `a0217eed5f775735d5ad01edf3141963be7b1787`
- master null seed: `20260928005`
- null draws per supported cell: `999`
- canonical verifier status: `CANONICAL_RESULT_PACKAGE_VERIFIED`

Run-manifest SHA-256:

- A2/A3 observed: `ab0db1b56c754ac4ccb20cc668209106e40c2c369bb989f3de31b54db6f8e59c`
- A4 observed: `7ccdf02a1f2b6ec4ea4558d765967c06123e7d83e856a59e7dd47da4627d933d`
- A5 observed: `c9ace53fece580f4181516e7360df924f4a39418063d0d825b2d6520c3d81214`
- N23: `8f172a2231bf23ea90792b0c849812aebab8c5dfa26c0c5930dc6ae9abf9db1d`
- N4: `ec2b448b53b3995354575d75088b17f6d4a34f378d217fb891e9c9de6e865bfa`
- N5: `445a70ee8277e89fcf427c6a47c7f747b3a92d1e669c20cfe23b52c1fe58395c`

## Headline evidence

- A1 exact role join: **374,891 / 374,891**.
- Post-V2 high-confidence role coverage: **99.468%**.
- A2 own-market: **0/10** BH rejections.
- A3 same-family: **1/10** BH rejections.
- A3 semantic: **0/10** BH rejections.
- A4 participant: **0/10** BH rejections.
- A5 maker: **0/10** BH rejections.
- A5 primary liquidity: **0/30** BH rejections.
- A5 capture placebo: **0/30** BH rejections.

The single promoted cell is A3 SAME_FAMILY / PRE_ELECTION / 5s:

- validation rows: 92,007
- relative MSE gain: 7.6555e-06 (0.0007655%)
- circular p: 0.005
- 300s block p: 0.002
- intersection p: 0.005
- BH q: 0.050

Per the frozen rule that a winning cell does not promote a mechanism, this is a replication candidate only.

## Canonical files

Use:

- `data/experiments/experiment_005a/results/FINAL_RESEARCH_REPORT.md`
- `data/experiments/experiment_005a/results/canonical_verification.json`
- `data/experiments/experiment_005a/results/artifact_hashes.json`
- compact observed/null CSV summaries in this directory;
- six copied run manifests under `results/run_manifests/`.

Large panels and raw null draws are intentionally not duplicated into Git. Their byte hashes remain pinned
by the copied run manifests and the private Kaggle outputs.

## Carry-forward boundary

Carry the role-enrichment infrastructure forward.

Carry **only** the same-family PRE 5s signed-flow effect as a frozen candidate for genuine forward
replication. Do not mine 005A for neighboring horizons, best pairs, wallets, thresholds, or strategy P&L.

Any later trading claim requires fresh/forward evidence and an effect large enough to survive actual
spread, fee and latency economics.
