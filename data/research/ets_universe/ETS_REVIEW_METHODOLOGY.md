# ETS discovery and semantic review provenance

- Base `main` SHA: `bbd152eb13f827b9ede36e444ac597eb0274443d`.
- Canonical mapping SHA-256: `9bc3d55317ace5e7e56eb0e4deaf2d36d09421b2861b7104977a96bedfc16df2`.
- Mapping acceptance SHA-256: `fa59331a45897f7971454835e86f67a65313a2efff8ca387a3b2cf1ea99b19e5`.
- Discovery kernel: `polyleviathan/r2-5-ets-universe-discovery`; successful Actions run 36517334774.
- Discovery fetched timestamp: see `ETS_DISCOVERY_AUDIT.json`.
- Candidate snapshot: 37,410 rows including direct mappings; raw candidate CSV SHA-256 is bound in the audit and review summary; raw snapshot remains in the lane/Kaggle artifact rather than Git.
- Gamma event snapshot: raw JSON is attached to the discovery kernel output; its SHA-256 and fetched timestamp are bound in the audit; raw snapshot remains outside Git.
- The candidate ledger records one explicit accept/reject status per discovered market. Acceptance requires verified Gamma identity, aligned outcomes/tokens, complete election/race identity, a 2026 election scope, and an explicit economic link to at least one accepted SIG anchor. State/title conflicts, local/non-US/policy markets, wrong or unproven cycles, and untradeable identities are rejected. Direct mapping duplicates remain anchors and are excluded from ETS. Each accepted edge has a controlled relationship class, direction, rationale, and confidence.
- Current reviewed counts: 18,265 adjacent ETS markets; 42,306 accepted edges; 18,544 rejected non-direct candidates; 601 direct duplicates; 0 pending. 13 missing-CID candidates were re-queried against Gamma and remain untradeable; two congressional-map candidates are relevant context but have no tradable condition/token identity.
- `ETS_REVIEW_EDGES.csv`, `ETS_REVIEW_SUMMARY.json`, and `ETS_UNRESOLVED_IDENTITY_CHECK.json` are the compact review evidence. The SHA of the full candidate snapshot and full review ledger is recorded in the summary/freeze.

No historical price/fill behavior informed membership decisions. Historical acquisition starts only after the membership freeze.
