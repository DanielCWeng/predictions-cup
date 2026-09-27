# EXPERIMENT-004C Research Index

This index canonicalizes the independent research artifacts prepared for EXPERIMENT-004C preregistration. It is an organizational record only: it does not reconcile hypotheses, alter classifications, change the scientific design, or add empirical evidence.

## Canonical 004B evidence base

- Integration PR: **#30 — EXPERIMENT-004B — Election-Market Structure Discovery**
- Canonical integration branch: `experiment/004b-master-integration`
- Canonical integration commit: `5b49d2ad13ea538fe9222a41643af7cf7d477c86`
- Frozen 004B implementation HEAD: `e9a95fb2092c7e7385ecf27f6972b8d8921c1f72`
- Frozen discovery-spec SHA-256: `409512367f59fefa861df89c57b96586d30e28d205f60e306b20a5e64935b504`

PR #30 is the canonical 004B evidence/provenance source and was merged to `main` at merge commit `7fd19a1e4259e01a30d172475184d15cf45f8725` following MASTER authorization. The documented Hungary ACTIVE circular-shift deviation remains part of the evidence record: 1,000 deterministic attempts produced only 648 valid contemporaneous statistics and 833 valid directed-30s statistics for the two affected aggregate statistics. The merge does not erase that limitation or change the underlying scientific results.

## Historical challenge-set prior exposure

The following events were sealed from EXPERIMENT-004B empirical inspection but had previously been touched by EXPERIMENT-003:

- Colombia first round
- Peru runoff
- Colombia runoff

Their canonical 004C status is `004B_SEALED_PRIOR_EXPERIMENT_EXPOSED`.

They remain usable as locked 004C replication/challenge data after preregistration, but they are **not globally pristine holdouts** and must not be described that way.

## Research lanes

### Opus

- Status: **COMPLETE**
- Source branch: `research/004c-opus-hypotheses`
- Source PR: **#29**
- Source commit: `f22592ce0e81f2efd712af601cc939596cbc042e`
- Pre-004B prior:
  - `data/experiments/experiment_004c/research/opus/OPUS_PRE_004C_RESEARCH_DOSSIER.md`
  - SHA-256: `438e695cb02a0644aa5ee060b500760378aa3e011a6b5ae1e37eda918dd77be4`
- Post-004B hypothesis set:
  - `data/experiments/experiment_004c/research/opus/OPUS_004C_HYPOTHESIS_SET.md`
  - SHA-256: `9b1e3521e97b91a4e0955bf8071329f9aa2dd0b357b1b2c3c8a69535b8629947`
- Checksum manifest:
  - `data/experiments/experiment_004c/research/opus/SHA256SUMS`

The files are copied byte-for-byte from PR #29; substantive content is unchanged.

### Astra

- Status: **PAUSED / PRIOR_ONLY**
- Source branch: `research/004c-astra-prior`
- Source PR: **#31**
- Source commit: `07eb1e6276c77f187c77164bbaadba96aba71a47`
- Pre-004B prior:
  - `data/experiments/experiment_004c/research/astra/ASTRA_PRE_004C_QUANT_RESEARCH_DOSSIER.md`
  - SHA-256: `3c8403c8a69d46486aa60074a89349afc1625abb02db47f694ad14f183fd7a93`
- Paused checkpoint:
  - `data/experiments/experiment_004c/research/astra/ASTRA_004C_PAUSED_CHECKPOINT.md`
  - SHA-256: `426891232d9fdd1f3d92d3dec3ff5e0e31c397bfe7b87864f95c00efd163070b`
- Checksum manifest:
  - `data/experiments/experiment_004c/research/astra/SHA256SUMS`

**Astra does not currently have a completed post-004B hypothesis set.** The dossier is the completed pre-004B prior; the checkpoint records the paused state and is not a substitute for `ASTRA_004C_HYPOTHESIS_SET.md`.

The files are copied byte-for-byte from PR #31; substantive content is unchanged.

### Sol

- Status: **COMPLETE (POST-004B HYPOTHESIS SET)**
- Source branch: `experiment/004c-sol-hypothesis-generation`
- Source PR: **#32**
- Source commit: `94974da5b8ef9ecd84056c1372ff327df7e7b45c`
- Post-004B hypothesis set:
  - `data/experiments/experiment_004c/research/sol/SOL_004C_HYPOTHESIS_SET.md`
  - SHA-256: `339ed6f3380e26aec61396a709efd96f9099f7ee44894f9c51ecbefaa18bcd27`
- Checksum manifest:
  - `data/experiments/experiment_004c/research/sol/SHA256SUMS`
- Pre-004B prior artifact: **none included in the Sol source PR**

The hypothesis-set bytes are preserved from PR #32; only the canonical repository path/name changes.

## Provenance and duplicate handling

The source PRs and branches remain intact as provenance. This cleanup does not delete them.

- PR #29 / `research/004c-opus-hypotheses`: source provenance; canonical content copied under `research/opus/`.
- PR #31 / `research/004c-astra-prior`: source provenance; canonical content copied under `research/astra/`.
- PR #32 / `experiment/004c-sol-hypothesis-generation`: source provenance; canonical content copied under `research/sol/`.
- PR #30 / `experiment/004b-master-integration`: canonical 004B integration evidence base.
- `experiment/004b-dynamics-stats`: retained as Lane-B provenance; superseded for integration purposes by PR #30.
- EC2-local Sol commit `00a210029387665edbf5a8776a87461f1dcc887d` contains an older provenance wording and 004B ancestry. It is not used here. Remote PR #32 commit `94974da5b8ef9ecd84056c1372ff327df7e7b45c` is the canonical Sol source for this cleanup.

No useful provenance is deleted. MASTER should consume the canonical paths in this index for 004C preregistration work.

## Validation

- CI: PASS — GitHub Actions run #915 completed successfully.
- Lane checksum manifests: PASS.
- Canonical path/index validation: PASS.
- `git diff --check`: PASS.

## Byte-preservation note

The canonical research Markdown is preserved byte-for-byte from its source PR blobs. The Sol source contains three intentional two-space Markdown hard breaks. A narrowly scoped `.gitattributes` rule disables `blank-at-eol` checking only for `data/experiments/experiment_004c/research/**/*.md`, allowing `git diff --check` to pass without rewriting the original research bytes.
