Polymarket infra address inventory
Network: Polygon mainnet / `chainId = 137`  
Reworked: 2026-07-06  
Update patch: 2026-07-08 fee/reward registry completion  
Previous inventory date: 2026-07-03  
Base indexer snapshot: 2026-07-01  
Primary code source of truth: `Sonar/custody_indexer.py` L32-L69  
Supporting notes: `docs/custody_indexer_findings.md`
This file is a PnL-safe infrastructure registry. It is not just an official deployment list. The purpose is to decide whether an address is a protocol/collateral endpoint that must be excluded or specially handled in wallet PnL, versus a contract that should only be tracked for metadata, identity attribution, resolution mapping, or future scans.
Decision rules
Do not bulk-import Polymarket-labeled PolygonScan addresses. Many are per-user Safe/proxy deposit wallets.
Official identity is not enough for `INFRA_ADDRESSES`. A contract can be official Polymarket infra but still not be a watched ERC-20/ERC-1155 cash/share leg endpoint.
Implementations usually do not belong in `INFRA_ADDRESSES`. Transfers normally emit from proxies, not implementation contracts.
Factories, beacons, and wallet implementations belong in the identity-attribution layer, not the cashflow exclude-set, unless a leg scan proves otherwise.
Resolution/oracle contracts belong in a resolution map first. Only promote to cash/share infra if transfer-leg scans show watched-path legs.
Vaults and collateral-boundary addresses require special treatment. They may not be traders, but they can appear in wrap/unwrap legs and contaminate PnL if ignored.
New candidates stay in backlog until verified on-chain and/or against first-party Polymarket sources, plus transfer-leg scan where relevant.
2026-07-06 rework summary
Change	Decision
Moved already-promoted addresses into the live infra section	`0xada100874d00e3331d00f2007a9c336a65009718`, `0xada200001000ef00d07553cee7006808f895c6f1`, and `0xf3cfb6a6ebfeb51876289eb235719eb1c65252b0` are now shown in Section 1, because the old notes already marked them as confirmed live in `custody_indexer.py`.
Added pUSD backing vault	`0xc417fd8e9661c0d2120b64a04bb3278c17e99db1` added as `COLLATERAL_VAULT`. Treat as a collateral-boundary endpoint; scan wrap/unwrap legs before deciding exact code handling. UPDATE 2026-07-08 (mechanism table): iso8 scan shows its big nets are user-neutral wrap/unwrap leg pairs; zero I2 effect on the tail conds. Do NOT INFRA-flag on tail evidence alone.
Added CtfAutoRedeem candidate	`0x05cd9922a5d37fae921fc5dee280a9dbc4c3b393` added as `REDEMPTION_INFRA_CANDIDATE_SCAN_FIRST`. It is verified on PolygonScan as `CtfAutoRedeem`, but it is not in Polymarket's official `contract-security` list, so do not auto-promote.
Added missing fee/reward recipients	Added the three missing direct fee-recipient wallets plus FeeDistributor, ProtocolFeeWallet, LiquidityRewardsDistributor, and HoldingRewardsDistributor to the fee/reward registry section. Existing fee-module rows were preserved; this is a registry completion patch, not an INFRA_ADDRESSES promotion.
Contract-security coverage	All official addresses from Polymarket's `contract-security` repo are represented somewhere in this file. Many remain intentionally outside `INFRA_ADDRESSES`.
Encoding cleanup	2026-07-09: restored valid UTF-8 (Windows-1252 smart punctuation → Unicode em dash / quotes / ≈). All 0x addresses byte-verified unchanged.
Live indexer infrastructure
These are the addresses the code currently treats, or should continue treating, as protocol infrastructure. `In INFRA = yes` means the address is a member of, or has already been confirmed as promoted into, `INFRA_ADDRESSES`.
1.1 Collateral tokens and wrapped collateral
Address	Label	In INFRA	Notes
`0x2791bca1f2de4661ed88a30c99a7a9449aa84174`	USDC.e / bridged USDC	yes	V1-era wallet cash token. Code constant: `USDC_E`.
`0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb`	pUSD / Polymarket USD / CollateralToken proxy	yes	V2-era wallet cash token. Code constant: `PUSD`. Migration date `2026-04-28` from dump remains unverified.
`0x3a3bd7bb9528e159577f7c2e685cc81a765002e2`	Wrapped Collateral / WCOL	yes	NegRisk-internal intermediate. Keep in infra/topic filters because conversion cash legs can originate from WCOL.
```python
COLLATERAL_TOKENS = {USDC_E, PUSD}
```
Only USDC.e and pUSD are indexed as cash collateral. WCOL is not a user cash token, but it must remain in `INFRA_ADDRESSES` / `INFRA_TOPICS` because conversion flows can use WCOL as the intermediate source of collateral.
1.2 Conditional Tokens Framework + redemption emitters
Address	Label	In INFRA	Notes
`0x4d97dcd97ec945f40cf65f87097ace5ea0476045`	ConditionalTokens / CTF / Gnosis CTF	yes	ERC-1155 outcome positions and `PayoutRedemption` emitter.
`0xd91e80cf2e7be2e162c6513ced06f1dd0da35296`	NegRisk Adapter	yes	Redemption emitter. Important: `payout_redemption.redeemer` is this contract, not the end user. **DEPLOY BLOCK 50,505,403** (MEASURED 2026-08-15, `eth_getCode` binary search; single non-proxy immutable deployment, same tx constructed its WrappedCollateral). Emits its OWN `PayoutRedemption(address,bytes32,uint256[],uint256)` topic0 `0x9140a6a2…` whose **topic1 = immediate caller, topic2 = conditionId** — NOT INDEXED as of 2026-08-15, see ADR-0044. Also emits unindexed `PositionSplit` `0xbbed930d…` and `PositionsMerge` `0xba33ac50…` (3-arg adapter variants, stakeholder = msg.sender).
```python
REDEEM_EMITTERS = [CTF, NEGRISK_ADAPTER]
```
1.3 Exchange paths
Address	Label	In INFRA	Notes
`0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e`	CTF Exchange V1	yes	Legacy CLOB exchange path.
`0xe111180000d2663c0091e4f400237545b87b996b`	CTF Exchange V2	yes	Current CTF exchange path from `ctf-exchange-v2`.
`0xc5d563a36ae78145c45a50134d48a1215220f80a`	NegRisk CTF Exchange V1	yes	Also referenced as `NEGRISK_EXCHANGE` in `pnl_common.py`.
`0xe2222d279d744050d28e00520010520000310f59`	NegRisk CTF Exchange V2	yes	Current NegRisk exchange path from `ctf-exchange-v2`.
1.3.1 OrderFilled / RPC ingestion notes
`Sonar/rpc_log_fetcher.py` is the code source for RPC trade-log ingestion.

Current exchange log-query set:
- CTF Exchange V1 / original: `0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e` (Sep 2022 -> Apr 2026)
- CTF Exchange V2 / current: `0xe111180000d2663c0091e4f400237545b87b996b` (Apr 2026 -> present)
- NegRisk CTF Exchange V1: `0xc5d563a36ae78145c45a50134d48a1215220f80a`
- NegRisk CTF Exchange V2: `0xe2222d279d744050d28e00520010520000310f59`

Standard binary exchange migration: V1 `0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e` handled Sep 2022 through Apr 2026; V2 `0xe111180000d2663c0091e4f400237545b87b996b` is the current path from roughly 2026-04-28 11:00 UTC. NegRisk has both V1 `0xc5d563a36ae78145c45a50134d48a1215220f80a` and V2 `0xe2222d279d744050d28e00520010520000310f59` exchange paths in current code.

OrderFilled topics in current code:
- V1 ABI: `0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6`
- V2 ABI: `0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee`

`fetch_orderfilled_logs()` queries both topics. `decode_any_order_filled()` dispatches by `topic0`; callers should use that dispatcher, not the V1 decoder directly. In current code, CTF V1 uses the V1 topic, CTF V2 uses the V2 topic, and NegRisk aliases the V1 topic unless emitted logs carry the V2 topic.
1.4 Adapters, ramps, and settlement plumbing
Address	Label	In INFRA	Notes
`0xada100db00ca00073811820692005400218fce1f`	CtfCollateralAdapter / earlier deployment	yes	Settlement/collateral adapter. Keep separate from the current `0xada100874...` deployment.
`0xada100874d00e3331d00f2007a9c336a65009718`	CtfCollateralAdapter / current repo deployment	yes	Promoted / confirmed live. Transfer-leg scan found net shares ≈0 across 1,128 tokens / 564 conditions and roughly `S-$4.74M` cash previously misattributed as user PnL; user payouts are separate `0x0 ? user` mints. Confirmed live in `custody_indexer.py:53`.
`0xada2005600dec949baf300f4c6120000bdb6eaab`	NegRiskCtfCollateralAdapter / earlier deployment	yes	NegRisk settlement/collateral adapter. Keep separate from the current `0xada200001...` deployment.
`0xada200001000ef00d07553cee7006808f895c6f1`	NegRiskCtfCollateralAdapter / current repo deployment	yes	Promoted / confirmed live. Confirmed in Polymarket's `ctf-exchange-v2` repo and PolygonScan. Splits/merges collateral, redeems, converts NO?YES through the NegRisk adapter. Data side: 165 conditions, `S-$516,254` cash misattributed, net shares ≈0 across 330 tokens. Confirmed live in `custody_indexer.py:55`.
`0xa1200000d0002264c9a1698e001292d00e1b00af`	AutoRedeemer proxy	yes	Official Polymarket V2 Auto Redeemer. Existing live inventory already tracks this proxy.
`0x93070a847efef7f70739046a929d47a521f5b8ee`	CollateralOnramp	yes	Wraps supported assets into pUSD/PMCT. Confidence upgraded to first-party repo-confirmed.
`0x2957922eb93258b93368531d39facca3b4dc5854`	CollateralOfframp	yes	Unwraps pUSD/PMCT back into supported assets. Confidence upgraded to first-party repo-confirmed.
`0xf3cfb6a6ebfeb51876289eb235719eb1c65252b0`	BinaryModule redemption router / **REPLACEMENT CtfAutoRedeem**	yes	🔴 **DEPLOY BLOCK 86,136,861 (2026-04-28, CLOB-V2 cutover) — MEASURED 2026-08-15** by `eth_getCode` binary search (absent 86,136,860, present 27,352 chars at 86,136,861). **This is the SUCCESSOR to `0x05cd9922…`**: Polymarket's TS SDK changed `autoRedeemOperator` 0x05cD9922→0xF3cFb6a6 on 2026-05-13 ("align auto-redeem operator with frontend"). Handover is TOTAL — MEASURED emissions of BinaryRedemption/NegRiskRedemption: 0x05cd 239/3,113 in Apr then **0** in May/Jun/Aug; 0xF3cf **0** pre-cutover then 4,119/1,978/244. 🔴 **GAP: this address is in INFRA_ADDRESSES (cash excluded) but `CTF_AUTOREDEEM` at `custody_indexer.py:70` is still `0x05cd9922…` ONLY, so stream 6 NEVER fetches this router's beneficiary events.** Excluded and never credited — the exact failure ADR-0012 names. Fix: make `CTF_AUTOREDEEM` a list. See ADR-0044 §3a. Promoted / confirmed live. Emits `BinaryRedemption`; redeems settled positions, receives USDC.e from CTF, wraps into pUSD, and mints pUSD back to original holders. Data side: 566 conditions, `S+$3,511,795` cash misattributed in Gate-3 sample, net shares ≈1e-10. Confirmed live in `custody_indexer.py:56`. Gross-flow recurrence scan still open.
1.5 Special endpoints
Address	Label	Notes
`0x0000000000000000000000000000000000000000`	Zero address	Mint/burn endpoint. Code constant: `ZERO_ADDR`. Also handled in exclude logic.
`0xc417fd8e9661c0d2120b64a04bb3278c17e99db1`	pUSD backing vault / underlying asset vault	PROMOTED 2026-07-11 FIX4. Fable-confirmed by `VAULT()` eth_call. Treat as collateral-boundary infrastructure; exclude as endpoint. Never in i2_net fee roster.
`0x05cd9922a5d37fae921fc5dee280a9dbc4c3b393`	CtfAutoRedeem	PROMOTED 2026-07-11 FIX4 from `REDEMPTION_INFRA_CANDIDATE_SCAN_FIRST` after `/tmp/grokfix2/SCAN_05cd.md`. Exclude the ERC-20 recipient endpoint, but attribute beneficiary by `BinaryRedemption` / `NegRiskRedemption` indexed redeemer/from.
`0xe3333700ca9d93003f00f0f71f8515005f6c00aa`	Exchange V3 / Combos Exchange proxy	PROMOTED 2026-07-11 FIX4. Endpoint-only infra; no special attribution wiring yet.
`0x30000034706c7d8e12009dab006be20000c031a8`	CombinatorialModule proxy	PROMOTED 2026-07-11 FIX4. Endpoint-only infra; no special attribution wiring yet.
1.6 Event topic0 signatures
Topic0	Event
`0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef`	ERC-20 `Transfer`
`0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62`	ERC-1155 `TransferSingle`
`0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb`	ERC-1155 `TransferBatch`
`0x2682012a4a4f1973119f1c9b90745d1bd91fa2bab387344f044cb3586864d18d`	CTF `PayoutRedemption`
Approved additions / promote at rebuild
These are not necessarily live in the deployed indexer yet. They have enough source support to carry into the next rebuild plan, but some still need leg scans before being added to the cash/share exclude-set.
Address	Label	Flag	Confidence	Impact / action
`0xebc2459ec962869ca4c0bd1e06368272732bcb08`	PermissionedRamp / native-USDC onramp	`ADD_AT_REBUILD_NATIVE_USDC_RAMP`	First-party `ctf-exchange-v2` deployed-contract list	Dormant in sampled window, but structurally important. Add proactively and gate `INFRA_TOPICS` so native-USDC/USDC.e ramp legs are not mistaken for user activity. Re-check whether native USDC is live during the 2026-06-23 ? present backfill.
`0x78769d50be1763ed1ca0d5e878d93f05aabff29e`	NegRisk FeeModule	`ADD_AT_REBUILD_FEE_MODULE`	Official `contract-security` deployment	No observed rebate legs in sampled window. Low PnL impact, but safe to carry as proactive rebuild addition. Still keep fee-module handling separate from ordinary cash/share infra until a leg scan proves direct watched legs.
`0xc417fd8e9661c0d2120b64a04bb3278c17e99db1`	pUSD backing vault / underlying asset vault	`PROMOTED_INFRA_COLLATERAL_VAULT`	pUSD source defines immutable `VAULT` as the address holding underlying assets; VAULT() eth_call confirmed by Fable 2026-07-11. PROMOTED 2026-07-11 FIX4 as collateral-boundary endpoint infra. **NEVER in i2_net fee roster** (FEE_ERAS 2026-07-09: pass-through wrap/unwrap, user-neutral).
```python
ADD_AT_REBUILD_INFRA = {
    "0xebc2459ec962869ca4c0bd1e06368272732bcb08": "PermissionedRamp / native-USDC onramp",
    "0x78769d50be1763ed1ca0d5e878d93f05aabff29e": "NegRisk FeeModule",
}

COLLATERAL_VAULTS_TO_SCAN = {
    "0xc417fd8e9661c0d2120b64a04bb3278c17e99db1": "pUSD backing vault / underlying asset vault",
}
```
---
Verified non-leg infrastructure: do not add to `INFRA_ADDRESSES`
These are real contracts or official protocol infrastructure, but they are not cash/share leg endpoints for the current indexer model.
Address	Label	Flag	Why not `INFRA_ADDRESSES`
`0x6bbcef9f7ef3b6c592c99e0f206a0de94ad0925f`	pUSD CollateralToken implementation	`DO_NOT_ADD_IMPL_BEHIND_PROXY`	Transfers emit from the pUSD proxy `0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb`, not the implementation. Confirmed 0 legs across sampled dense days.
`0x00000000000fb5c9adea0298d729a0cb3823cc07`	Deposit Wallet Factory	`IDENTITY_INFRA_ONLY`	CREATE2 deployer. Identity layer, not a cash/share leg endpoint.
`0xaacfeea03eb1561c4e67d661e40682bd20e3541b`	Gnosis Safe Factory	`IDENTITY_INFRA_ONLY`	Factory/deployer. Track for proxy ownership attribution, not cashflow exclusion.
`0xab45c5a4b0c941a2f231c04c3f49182e1a254052`	Polymarket Proxy Factory / old	`IDENTITY_INFRA_ONLY`	Factory/deployer. Correct spelling is `...5a4b...`, not the known third-party typo `...54ab...`.
`0x6a9d222616c90fca5754cd1333cfd9b7fb6a4f74`	UMA Adapter	`RESOLUTION_INFRA_NO_LEGS_CONFIRMED`	Resolution infrastructure. Confirmed 0 watched cash/share legs in sampled dense days.
`0xcb1822859cef82cd2eb4e6276c7916e692995130`	UMA Optimistic Oracle	`RESOLUTION_INFRA_NO_LEGS_CONFIRMED`	Oracle infrastructure. Confirmed 0 watched cash/share legs in sampled dense days.
---
Flagged candidates and tracked official modules
4.1 Flag legend
Flag	Meaning	Default action
`SCAN_CASH_ENDPOINT`	May be a direct value-transfer endpoint or vault-like address.	Run ERC-20/ERC-1155 transfer-leg scans before adding to `INFRA_ADDRESSES`.
`RESOLUTION_INFRA`	Resolution/oracle adapter infrastructure.	Track in resolution map; only add to `INFRA_ADDRESSES` if transfer scan proves watched-path legs.
`IDENTITY_INFRA`	Factory/beacon/proxy infrastructure for wallet attribution.	Track in proxy/deposit wallet attribution layer, not the cashflow exclude-set unless proven as leg endpoint.
`ALT_DEPLOYMENT`	Alternate/parallel deployment of a contract type already represented.	Segment by deployment epoch and observed activity; do not collapse same-label contracts.
`COMBOS_MODULE`	Combos/V2 protocol module, exchange, router, or implementation.	Keep in separate Combos module list; scan transfer legs before treating as protocol counterparty.
`NEGRISK_PROTOCOL`	NegRisk protocol internal contract.	Track separately; scan for transfer legs before adding to `INFRA_ADDRESSES`.
`REDEMPTION_INFRA_CANDIDATE`	Redemption helper/router outside current confirmed live set.	Verify official source/owner and run transfer-leg scan before promotion.
`PERPS_INFRA_CANDIDATE`	Perps contract family.	Silo from spot-market PnL unless overlap scan proves contamination.
4.2 Resolution infrastructure candidates
Address	Label	Flag	Bucket	Review action
`0x157ce2d672854c848c9b79c49a8cc6cc89176a49`	UMA CTF Adapter v3.0	`RESOLUTION_INFRA`	`RESOLUTION_INFRA`	Add to resolution-infra map. Scan ERC-20/ERC-1155 legs before cashflow exclusion.
`0x2f5e3684cb1f318ec51b00edba38d79ac2c0aa9d`	NegRiskUmaCtfAdapter	`RESOLUTION_INFRA_OFFICIAL`	`RESOLUTION_INFRA`	Reclassified from candidate to official resolution infra on 2026-07-05. Still do not add to cash-exclusion `INFRA_ADDRESSES` unless a leg scan proves it emits/receives watched collateral/share legs. Official identity ? automatic cash-infra promotion.
4.3 Wallet identity infrastructure
Address	Label	Flag	Bucket	Review action
`0x7a18edfe055488a3128f01f563e5b479d92ffc3a`	Deposit Wallet Beacon	`IDENTITY_INFRA`	`WALLET_IDENTITY_INFRA`	Track for deposit-wallet proxy attribution and beacon upgrades. Do not add to cashflow exclude-set unless proven as a leg endpoint.
`0x8d3ae0afbacfde6f75571f78899c1b45d597dd78`	Deposit Wallet implementation / audited deployment	`IDENTITY_INFRA`	`WALLET_IDENTITY_INFRA`	Official `contract-security` address. Same treatment: identity layer only; do not add to cashflow `INFRA_ADDRESSES` unless a leg scan proves it is a transfer-leg endpoint.
```python
DEPOSIT_WALLET_IDENTITY_INFRA = {
    "0x00000000000fb5c9adea0298d729a0cb3823cc07": "Deposit Wallet Factory",
    "0x7a18edfe055488a3128f01f563e5b479d92ffc3a": "Deposit Wallet Beacon",
    "0x8d3ae0afbacfde6f75571f78899c1b45d597dd78": "Deposit Wallet implementation / audited deployment",
}
```
4.4 NegRisk protocol internals
Address	Label	Flag	Bucket	Review action
`0x71523d0f655b41e805cec45b17163f528b59b820`	NegRiskOperator	`NEGRISK_PROTOCOL`	`NEGRISK_PROTOCOL_INFRA`	Official `contract-security` address. Track as protocol infra. Scan transfer legs before adding to `INFRA_ADDRESSES`.
`0x7f67327e88c258932d7d8f72950be0d46975e11d`	NegRiskVault	`SCAN_CASH_ENDPOINT`	`NEGRISK_PROTOCOL_INFRA`	High-priority scan: vault-like address may be a value endpoint.
4.5 Combos / Polymarket V2 module candidates
These are official/tracked modules, but they should not automatically enter the spot binary/NegRisk PnL infra set. TASK8 found 0 appearances for most of the module family across all 55,379 tail-touching tx receipts. The Router remains not yet scanned.
Address	Label	Flag	Bucket	Review action
`0x006f54f7f9a22e0000cc2ab60031000000ae9fef`	PositionManager proxy	`COMBOS_MODULE_DO_NOT_PROMOTE`	`COMBOS_INFRA`	🔴 **NEVER add to `INFRA_ADDRESSES`** — this address IS `COMBO_ERC1155` (`custody_indexer.py:61`), the ERC-1155 *token contract* for combo positions. MEASURED 2026-08-20 over 38 days: appears only as `token_addr`, ZERO appearances as `from_address`/`to_address`. Treating it as a wallet is a category error. Scanned 2026-07-05 / TASK8: 0 appearances.
`0x30c038f0dae8dcc3e6ad51d016f50821d32cb87e`	PositionManager implementation	`COMBOS_MODULE`	`COMBOS_INFRA`	TASK8: 0 appearances. See PositionManager proxy row.
`0x1000008dd9001b968442c1000017eae6e0da00ba`	BinaryModule proxy	`COMBOS_MODULE_PROMOTED_INFRA`	`COMBOS_INFRA`	**PROMOTED 2026-08-20** to `INFRA_ADDRESSES` (endpoint-only; no attribution wiring needed). TASK8's "0 appearances" was pre-mechanism — MEASURED first appearance 2026-06-11, USDC.e in $19,125.51 / out $19,125.51 = net $0.00 over 38 held days. Exactly-balanced conduit. Receipt proof user money is not lost: tx `0x7a205a43…9161` CTF -> BinaryModule -> pUSD vault `0xc417fd8e`, user credited by a separate ZERO -> user pUSD mint of the same amount in the same tx; legacy-migration tx `0x3c9ce2cc…18aa` has no user leg (collateral pools in the vault, user credited on later redemption). Both credit paths ride the unfiltered pUSD stream.
`0x492fec596ec347459e1ebe30b9245eb3b49b1bba`	BinaryModule implementation	`COMBOS_MODULE`	`COMBOS_INFRA`	TASK8: 0 appearances. See PositionManager proxy row.
`0x200000900045e3b6259600682756002200028933`	NegRiskModule proxy	`COMBOS_MODULE_PROMOTED_INFRA`	`COMBOS_INFRA`	**PROMOTED 2026-08-20** to `INFRA_ADDRESSES`. MEASURED first appearance 2026-06-11; pUSD $1.00 in/out, WCOL payout_redemption $63,972.90 (WCOL already excluded). 14 staged holdings rows on 06-24/06-25 that cancel exactly. External source review: verified ABI is beneficiary-aware (`_to` / `_to[]`) but only 56 direct tx / 42 reverted — near-zero blast radius.
`0xa61e7ca374f721d5b9fd5b0fee6fb90f27d448d7`	NegRiskModule implementation	`COMBOS_MODULE`	`COMBOS_INFRA`	TASK8: 0 appearances. See PositionManager proxy row.
`0x30000034706c7d8e12009dab006be20000c031a8`	CombinatorialModule proxy	`COMBOS_MODULE_PROMOTED_INFRA`	`COMBOS_INFRA`	PROMOTED 2026-07-11 FIX4 as endpoint-only infra. No attribution wiring yet.
`0xb529b2430d78868422c47934d9d61cc9d0c53dbb`	CombinatorialModule implementation	`COMBOS_MODULE`	`COMBOS_INFRA`	TASK8: 0 appearances. See PositionManager proxy row.
`0xe3333700ca9d93003f00f0f71f8515005f6c00aa`	Exchange V3 / Combos Exchange proxy / official V2 Exchange in `contract-security`	`COMBOS_MODULE_PROMOTED_INFRA`	`COMBOS_INFRA`	PROMOTED 2026-07-11 FIX4 as endpoint-only infra. Keep separate from `0xe111...` CTFExchangeV2 and `0xe222...` NegRiskCtfExchangeV2.
`0x7345c6842b244926125ed4054905cac49620b5dc`	Combos Exchange implementation	`COMBOS_MODULE`	`COMBOS_INFRA`	TASK8: 0 appearances. See Combos Exchange proxy row.
`0x64860bfd14fccaac09cd36f347784a9616afb66c`	AutoRedeemer implementation	`COMBOS_MODULE`	`COMBOS_INFRA`	TASK8: 0 appearances. Existing live inventory tracks the AutoRedeemer proxy `0xa1200000d0002264c9a1698e001292d00e1b00af`; do not add the implementation as the main infra address.
`0x12121212006e4cd160d18e3f00711da5c3372600`	Router / Polymarket V2	`NEW_05_07_26_ROUTER`	`COMBOS_INFRA`	Official `contract-security` address. **SCANNED 2026-08-20** (2026-05-20 -> 06-26, 38 held days): ZERO appearances as `from_address`/`to_address`, including on the unfiltered pUSD stream. Held out of `INFRA_ADDRESSES` deliberately — adding it would change nothing, and the proxy upgraded 2026-07-20 with no post-upgrade receipt verified. External review: combo fills call Exchange V3 directly (`matchOrdersAndPrepareCombinatorial`), Router absent from the value path; historically `PURE_DISPATCHER` (ERC-1155 `operator`, never from/to). Revisit only if it appears as a leg.
4.6 Perps
Address	Label	Flag	Bucket	Review action
`0xdca4af75705dbb50f62437045aff9921947917d2`	Polymarket Perps	`NEW_05_07_26_PERPS`	`PERPS_SEPARATE_SILO`	Official `contract-security` address. Separate PERPS silo, not spot `INFRA_ADDRESSES`. Before any use, check whether this address or related perps contracts appear as counterparties on CTF ERC-1155, pUSD, USDC.e, PositionManager, Router, Deposit Wallet, CTFExchangeV2, or NegRiskCtfExchangeV2 legs. If zero overlap: silo completely. If overlap exists: escalate as contamination candidate.
```python
PERPS_INFRA_CANDIDATES = {
    "0xdca4af75705dbb50f62437045aff9921947917d2": "Polymarket Perps",
}
```
4.7 Fee module candidates
Address	Label	Flag	Bucket	Review action
`0x78769d50be1763ed1ca0d5e878d93f05aabff29e`	NegRisk FeeModule	`ADD_AT_REBUILD_FEE_MODULE`	`FEE_MODULE_CANDIDATE`	Already tracked in Section 2. No observed rebate legs in sampled window. RESOLVED 2026-07-08: legacy generation of the NegRisk fee module; superseded by v2 0xb768891e... - see that row + ADR-0014 amendment.
`0x56c79347e95530c01a2fc76e732f9566da16e113`	FeeModule / V1 non-NegRisk	`I2_NET_USDC_ERA_BUCKETED`	`FEE_MODULE_I2_ROSTER`	RESOLVED 2026-07-09 (FEE_ERAS.md / ADR-0014): INCLUDE USDC leg only, era-bucketed **2023-05-04 → 2024-12-22** ($6,762.43 USDC lifetime). Share leg (+50,746 shares) EXCLUDED — share-backed take, same class as 0xd4aa6f8e. NOT INFRA.
`0xe3f18acc55091e2c48d883fc8c8413319d4ab7b0`	FeeModule (VERIFIED: PolygonScan label "Polymarket: Fee Module", contract name FeeModule; GitHub release Polygon deployment; fee-aware matchOrders proxy, fee = feeRateBps x min(price,1-price) x outcomeTokens, refunds over-charge)	`NEW_08_07_26_FEE_MODULE`	`FEE_MODULE_CANDIDATE`	Discovered via 620f isolation (ADR-0014, /tmp/tailmap/ISO620_REPORT.md): net +$70,948.64 collateral across 0x620f2c52 txs (6,047 legs, all single-cond), zero-share-delta fee legs + share-skim resale. Per ADR-0014 this class must NOT be promoted to INFRA_ADDRESSES; it belongs in the FEE_MODULES registry for the I2 fee term. Re-screen every lake extension.
`0xb768891e3130f6df18214ac804d4db76c2c37730`	NegRiskFeeModule (official GitHub Polygon deployment)	`NEW_08_07_26_FEE_MODULE`	`FEE_MODULE_CANDIDATE`	Paired with FeeModule 0xe3f18acc... per Polymarket release table (Daniel, 2026-07-08). No legs observed in 620f flow scan; scan population-wide before use in the I2 fee term. Amoy testnet deployments share the same addresses - irrelevant to this Polygon-only lake. RESOLVED 2026-07-08 (Daniel, on-chain): 0x78769d50be... = LEGACY NegRiskFeeModule, this address = v2 ("Neg Risk Fee Module 2", GitHub v2.0.0 release; explicit takerFeeAmount calldata + FeeRefunded escrow/refund path). Two GENERATIONS of one venue - never sum both as independent sinks; bucket by tx.to + era; use SIGNED NET flows so escrow refunds cancel. See ADR-0014 amendment.
`0x115f48dc2a731aa16251c6d6e1befc42f92accc9`	Fee Recipient (pUSD-era skim; DefiLlama Polymarket revenue adapter classifies it as Fee Recipient - Daniel, 2026-07-08)	`I2_NET_ACTIVE`	`FEE_MODULE_I2_ROSTER`	ACTIVE in i2_net roster (Daniel directive 2026-07-08 #2). Era **2026-04-03 → lake end** (FEE_ERAS: $1.66M net / $59.5M gross). iso8: zero-share-delta value-proportional collateral skim. NOT INFRA.
`0xf21a25dd01cca63a96adf862f4002d1a186decb2`	Fee Recipient / old	`I2_NET_ERA_BUCKETED`	`FEE_MODULE_I2_ROSTER`	RESOLVED 2026-07-09 (FEE_ERAS.md / ADR-0014): INCLUDE era-bucketed **2026-01-06 → 2026-01-15** hard-closed ($842,478.34 net USDC, 4,391 legs; zero activity after). NOT INFRA.
`0xd4aa6f8e91cfea29b66a48ebff523aafbdbbd40c`	Fee Recipient / main	`NEW_08_07_26_FEE_RECIPIENT`	`FEE_REWARD_ACCOUNTING`	DefiLlama Polymarket fee adapter lists this under FeeRecipients as the main recipient. Registry-only unless a transfer-leg scan proves a concrete watched-leg role.
`0x525e4001f6dad9406dfd84f3331d2b9b95c40b73`	Fee Recipient / NegRisk	`NEW_08_07_26_FEE_RECIPIENT`	`FEE_REWARD_ACCOUNTING`	DefiLlama Polymarket fee adapter lists this under FeeRecipients as the NegRisk recipient. Treat as fee/reward accounting, not user economic intent.
`0x3a9418b2651c8164db5ebc56f12008137865e0f7`	FeeDistributor	`NEW_08_07_26_FEE_REWARD_ACCOUNTING`	`FEE_REWARD_ACCOUNTING`	DefiLlama Polymarket fee adapter tracks this as FeeDistributor. Keep outside trader universe; scan for distribution legs if reconciling protocol-fee / rebate paths.
`0x2d507657ca4ebcc8f9a38f6764c07310b66dea54`	ProtocolFeeWallet	`NEW_08_07_26_FEE_REWARD_ACCOUNTING`	`FEE_REWARD_ACCOUNTING`	DefiLlama Polymarket fee adapter tracks this as ProtocolFeeWallet. Registry-only unless direct watched transfer legs affect the target PnL substrate.
`0xc288480574783bd7615170660d71753378159c47`	LiquidityRewardsDistributor	`NEW_08_07_26_FEE_REWARD_ACCOUNTING`	`FEE_REWARD_ACCOUNTING`	DefiLlama Polymarket fee adapter tracks this as LiquidityRewardsDistributor. Reward distribution accounting; do not collapse into exchange/fee-module execution routing.
`0xc536633ff12ee52e280b2af2594031060c5aaf41`	HoldingRewardsDistributor	`NEW_08_07_26_FEE_REWARD_ACCOUNTING`	`FEE_REWARD_ACCOUNTING`	DefiLlama Polymarket fee adapter tracks this as HoldingRewardsDistributor. Reward distribution accounting; scan separately from maker/taker fee skims.
```python
FEE_MODULE_CANDIDATES = {
    # Direct fee recipients / reward accounting endpoints
    "0x115f48dc2a731aa16251c6d6e1befc42f92accc9": "Fee Recipient (pUSD-era skim) — i2_net ACTIVE",
    "0xf21a25dd01cca63a96adf862f4002d1a186decb2": "Fee Recipient / old — i2_net era 2026-01-06→2026-01-15",
    "0xd4aa6f8e91cfea29b66a48ebff523aafbdbbd40c": "Fee Recipient / main — NOT i2_net (share-backed)",
    "0x525e4001f6dad9406dfd84f3331d2b9b95c40b73": "Fee Recipient / NegRisk — NOT i2_net (share-backed)",
    "0x3a9418b2651c8164db5ebc56f12008137865e0f7": "FeeDistributor — NOT i2_net (plumbing)",
    "0x2d507657ca4ebcc8f9a38f6764c07310b66dea54": "ProtocolFeeWallet — NOT i2_net (downstream)",
    "0xc288480574783bd7615170660d71753378159c47": "LiquidityRewardsDistributor — NOT i2_net (out-of-tx)",
    "0xc536633ff12ee52e280b2af2594031060c5aaf41": "HoldingRewardsDistributor — NOT i2_net (out-of-tx)",

    # Fee modules / execution wrappers
    "0x78769d50be1763ed1ca0d5e878d93f05aabff29e": "NegRisk FeeModule / legacy — i2_net era-bucketed",
    "0xb768891e3130f6df18214ac804d4db76c2c37730": "NegRiskFeeModule v2 — i2_net era-bucketed",
    "0x56c79347e95530c01a2fc76e732f9566da16e113": "FeeModule V1 — i2_net USDC-only era 2023-05-04→2024-12-22",
    "0xe3f18acc55091e2c48d883fc8c8413319d4ab7b0": "FeeModule / pUSD-era — i2_net ACTIVE",
}

# ADR-0014 i2_net roster (2026-07-09 final). Era bounds = fee_era_summary.parquet.
# 0xc417fd8e pUSD vault = pass-through wrap/unwrap — NEVER in this roster.
I2_NET_FEE_ROSTER = {
    "0xe3f18acc55091e2c48d883fc8c8413319d4ab7b0": ("2026-01-06", None),          # open
    "0x78769d50be1763ed1ca0d5e878d93f05aabff29e": ("2024-03-27", "2025-03-11"),
    "0xb768891e3130f6df18214ac804d4db76c2c37730": ("2026-03-04", None),
    "0x115f48dc2a731aa16251c6d6e1befc42f92accc9": ("2026-04-03", None),          # ACTIVE
    "0xf21a25dd01cca63a96adf862f4002d1a186decb2": ("2026-01-06", "2026-01-15"), # hard-closed
    "0x56c79347e95530c01a2fc76e732f9566da16e113": ("2023-05-04", "2024-12-22"), # USDC leg only
}
I2_NET_FEE_NEVER = {
    "0xc417fd8e9661c0d2120b64a04bb3278c17e99db1": "pUSD backing vault — pass-through, never fee sink",
    "0xd4aa6f8e91cfea29b66a48ebff523aafbdbbd40c": "main share-backed recipient",
    "0x525e4001f6dad9406dfd84f3331d2b9b95c40b73": "negRisk share-backed recipient",
}
```
4.8 New redemption candidate added 2026-07-06
Address	Label	Flag	Bucket	Review action
`0x05cd9922a5d37fae921fc5dee280a9dbc4c3b393`	CtfAutoRedeem / alt redemption helper	`PROMOTED_INFRA_EVENT_ATTRIBUTED`	`REDEMPTION_INFRA`	PROMOTED 2026-07-11 FIX4 after `/tmp/grokfix2/SCAN_05cd.md`. PolygonScan verifies `CtfAutoRedeem`; ABI includes `redeemBinary`, `redeemNegRisk`, `BinaryRedemption`, and `NegRiskRedemption`. Do not merge with official AutoRedeemer `0xa120...b00af`: this router is endpoint-excluded, with beneficiary attribution from the redemption event indexed redeemer/from.
```python
REDEMPTION_INFRA_CANDIDATES = {
    "0x05cd9922a5d37fae921fc5dee280a9dbc4c3b393": "CtfAutoRedeem / alt redemption helper",
}
```
4.9 Python review constants
```python
TRACKED_NON_LIVE_OR_SCAN_FIRST = {
    # Resolution / oracle infra
    "0x157ce2d672854c848c9b79c49a8cc6cc89176a49": "UMA CTF Adapter v3.0",
    "0x2f5e3684cb1f318ec51b00edba38d79ac2c0aa9d": "NegRiskUmaCtfAdapter",

    # Wallet identity infra
    "0x7a18edfe055488a3128f01f563e5b479d92ffc3a": "Deposit Wallet Beacon",
    "0x8d3ae0afbacfde6f75571f78899c1b45d597dd78": "Deposit Wallet implementation / audited deployment",

    # NegRisk protocol internals
    "0x71523d0f655b41e805cec45b17163f528b59b820": "NegRiskOperator",
    "0x7f67327e88c258932d7d8f72950be0d46975e11d": "NegRiskVault",

    # Combos / V2 modules
    "0x006f54f7f9a22e0000cc2ab60031000000ae9fef": "PositionManager proxy",
    "0x30c038f0dae8dcc3e6ad51d016f50821d32cb87e": "PositionManager implementation",
    "0x1000008dd9001b968442c1000017eae6e0da00ba": "BinaryModule proxy",
    "0x492fec596ec347459e1ebe30b9245eb3b49b1bba": "BinaryModule implementation",
    "0x200000900045e3b6259600682756002200028933": "NegRiskModule proxy",
    "0xa61e7ca374f721d5b9fd5b0fee6fb90f27d448d7": "NegRiskModule implementation",
    "0x30000034706c7d8e12009dab006be20000c031a8": "CombinatorialModule proxy",
    "0xb529b2430d78868422c47934d9d61cc9d0c53dbb": "CombinatorialModule implementation",
    "0xe3333700ca9d93003f00f0f71f8515005f6c00aa": "Combos Exchange proxy / official V2 Exchange",
    "0x7345c6842b244926125ed4054905cac49620b5dc": "Combos Exchange implementation",
    "0x64860bfd14fccaac09cd36f347784a9616afb66c": "AutoRedeemer implementation",
    "0x12121212006e4cd160d18e3f00711da5c3372600": "Router / Polymarket V2",

    # Perps
    "0xdca4af75705dbb50f62437045aff9921947917d2": "Polymarket Perps",

    # Fee modules / fee-reward accounting
    "0x56c79347e95530c01a2fc76e732f9566da16e113": "FeeModule / V1 non-NegRisk",
    "0xe3f18acc55091e2c48d883fc8c8413319d4ab7b0": "FeeModule / pUSD-era",
    "0xb768891e3130f6df18214ac804d4db76c2c37730": "NegRiskFeeModule v2",
    "0xf21a25dd01cca63a96adf862f4002d1a186decb2": "Fee Recipient / old",
    "0xd4aa6f8e91cfea29b66a48ebff523aafbdbbd40c": "Fee Recipient / main",
    "0x525e4001f6dad9406dfd84f3331d2b9b95c40b73": "Fee Recipient / NegRisk",
    "0x3a9418b2651c8164db5ebc56f12008137865e0f7": "FeeDistributor",
    "0x2d507657ca4ebcc8f9a38f6764c07310b66dea54": "ProtocolFeeWallet",
    "0xc288480574783bd7615170660d71753378159c47": "LiquidityRewardsDistributor",
    "0xc536633ff12ee52e280b2af2594031060c5aaf41": "HoldingRewardsDistributor",

    # New 2026-07-06 candidates
    "0xc417fd8e9661c0d2120b64a04bb3278c17e99db1": "pUSD backing vault / underlying asset vault",
    "0x05cd9922a5d37fae921fc5dee280a9dbc4c3b393": "CtfAutoRedeem / alt redemption helper",
}
```
---
Source coverage check
5.1 Polymarket `contract-security` coverage
Status: complete as of 2026-07-06. Every official address from the current Polymarket `contract-security` README is represented somewhere in this inventory.
Source group	Inventory status
V1 contracts	Covered: Proxy Factory, Safe Factory, Conditional Tokens, CtfExchange, NegRisk Adapter, NegRisk Operator, WCOL, NegRisk CtfExchange, NegRisk FeeModule, NegRisk UmaCtfAdapter, UmaCtfAdapter, FeeModule.
Polymarket V2	Covered: Exchange / Combos Exchange, Collateral Token, Position Manager, Router, Binary Module, NegRisk Module, Combinatorial Module, Auto Redeemer.
Deposit Wallet	Covered: Deposit Wallet implementation and Deposit Wallet Factory. Beacon is also tracked separately.
Perps	Covered as siloed `PERPS_INFRA_CANDIDATE`.
5.2 Polymarket `ctf-exchange-v2` coverage
Status: complete as of 2026-07-06. The first-party `ctf-exchange-v2` Polygon deployed-contract list is represented here.
`ctf-exchange-v2` contract	Address	Inventory treatment
CollateralToken implementation	`0x6bbcef9f7ef3b6c592c99e0f206a0de94ad0925f`	Do not add implementation behind proxy.
CollateralToken proxy / pUSD	`0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb`	Live collateral token.
CollateralOnramp	`0x93070a847efef7f70739046a929d47a521f5b8ee`	Live infra.
CollateralOfframp	`0x2957922eb93258b93368531d39facca3b4dc5854`	Live infra.
PermissionedRamp	`0xebc2459ec962869ca4c0bd1e06368272732bcb08`	Add at rebuild / native-USDC path.
CtfCollateralAdapter	`0xada100874d00e3331d00f2007a9c336a65009718`	Promoted / confirmed live.
NegRiskCtfCollateralAdapter	`0xada200001000ef00d07553cee7006808f895c6f1`	Promoted / confirmed live.
CTFExchangeV2	`0xe111180000d2663c0091e4f400237545b87b996b`	Live exchange path.
NegRiskCtfExchangeV2	`0xe2222d279d744050d28e00520010520000310f59`	Live exchange path.
5.3 Why official lists still do not fully solve PnL reconciliation
Official deployment/security lists are not the same as a PnL-safe transfer-leg registry. They often omit or de-emphasize:
constructor/immutable vault addresses,
proxy implementation metadata,
old helper contracts,
module implementations,
dynamically deployed user deposit wallets,
product silos like Perps,
and addresses that only become obvious after transaction-walk analysis.
That is why this file must keep both a source classification and a PnL action for every address.
---
Reconciliation / code-drift notes
6.1 Confirmed live promotions from old notes
The prior inventory already said these were promoted and confirmed live in `Sonar/custody_indexer.py`; the rework simply moves them into the live section instead of leaving them buried under candidates:
`0xada100874d00e3331d00f2007a9c336a65009718` — CtfCollateralAdapter alt/current repo deployment — confirmed live at `custody_indexer.py:53`.
`0xada200001000ef00d07553cee7006808f895c6f1` — NegRiskCtfCollateralAdapter alt/current repo deployment — confirmed live at `custody_indexer.py:55`.
`0xf3cfb6a6ebfeb51876289eb235719eb1c65252b0` — BinaryModule redemption router — confirmed live at `custody_indexer.py:56`.
6.2 Dormant drift in `rpc_log_fetcher.py`
`Sonar/rpc_log_fetcher.py` has its own smaller `POLYMARKET_INFRA_ADDRESSES` frozenset with only five addresses: CTF Exchange V1/V2, NegRisk Exchange, CTF, and NegRisk Adapter. It does not import the shared `INFRA_ADDRESSES` from `custody_indexer.py` and is missing the vault/BinaryModule/alt-adapter promotions above.
Impact assessment from prior notes:
It tags an `is_infrastructure` boolean on SPLIT/MERGE events.
CONVERSION events hardcode `is_infrastructure=False`, so this drift does not affect conversion handling.
The only traced consumer that acts on this flag is `fifo_pnl_corrector.py`, which skips “Infrastructure SPLITs” when `is_infrastructure=True`.
No active job or worker (`Sonar/jobs/*`, `worker.py`) calls it.
It appears to be legacy FIFO PnL machinery from ADR-0002/0007, superseded by the custody-based substrate from ADR-0008.
Conclusion: real drift, but dormant. Clean it up eventually by deleting the dead module or pointing it to the shared `INFRA_ADDRESSES`; not urgent for the live I2 tail investigation.
---
Event-decode decision notes
7.1 `PositionsConverted` / NegRisk multi-outcome
Status: resolved as not a defect.
`PositionsConverted` carries no standalone value. The actual movement is captured through sub-legs:
ERC-1155 batch burns from the user,
collateral release via `WCOL ? user` transfer,
complement YES transfer from adapter ? user.
Reference transaction from prior analysis:
```text
0x34bc68840cc166174096edb1ced67ec388d1c0d0bce506a8218ff55c7b674f5c
```
Topic/signature:
```text
topic0: 0xb03d19dddbc72a87e735ff0ea3b57bef133ebe44e1894284916a84044deb367e
sig: PositionsConverted(address indexed stakeholder, bytes32 indexed marketId, uint256 indexed indexSet, uint256 amount)
```
Critical invariant:
```text
WCOL must stay in INFRA_ADDRESSES / INFRA_TOPICS.
```
If WCOL is removed from the USDC.e counterparty filter, conversion inflows can vanish from custody while positions still move, creating a phantom cash sink.
7.2 `PositionSplit` / `PositionsMerge`
Status: open, but no evidence of a current gap.
The same captured-via-sublegs logic appears to apply. ERC-1155 legs are captured, while split/merge semantics are inferred via floor-at-zero rather than decoded directly.
Known topic0s:
```text
PositionSplit:  0xbbed930d...
PositionsMerge: 0xba33ac50...
```
---
Wallet identity / proxy fragmentation
The raw PnL key is currently the raw `from_address` / `to_address`, which means one person can fragment across multiple rows if they trade through multiple proxy/deposit wallets, or through both an EOA and a proxy wallet.
Factories and beacons belong in a separate attribution layer:
```text
WALLET_IDENTITY_INFRA != INFRA_ADDRESSES
```
Recommended downstream work:
Resolve proxy wallets back to canonical owners.
Use `safeproxyfactory_evt_proxycreation` and the old `0xab45...` factory where applicable.
Include deposit-wallet factory/beacon mapping for newer deposit wallets.
Roll up only after the `(wallet, condition)` substrate is trusted.
PolygonScan Polymarket label pass — role triage
Do not bulk-import PolygonScan Polymarket-labeled wallets into `INFRA_ADDRESSES`. The label page contains many per-user wallets/deposit addresses.
9.1 Confirmed user Safe/proxy deposit wallets — attribution only
Address	Notes
`0xfd995f99eaec2139a7d8984a38ddd5ba442cbc32`	Polymarket Deposit Address, GnosisSafeProxy, created by Polymarket Safe Proxy Factory. Do not add to `INFRA_ADDRESSES`.
`0x26493cd2cdd8d2ae0e04e43274097e1477d5a388`	Polymarket Deposit Address, GnosisSafeProxy, implementation `0xe51abdf814f8854941b9fe8e3a4f65cab4e7a4a8`, created by Polymarket Safe Proxy Factory. Do not add to `INFRA_ADDRESSES`.
`0x2e91439a0b1cead4258e3ab56030f00cce6b85aa`	Polymarket Deposit Address, GnosisSafeProxy, created by Polymarket Safe Proxy Factory. Do not add to `INFRA_ADDRESSES`.
`0x388911e52bb2eb440b9f03ed24bcef13c93e1499`	Polymarket Deposit Address, GnosisSafeProxy, created by Polymarket Safe Proxy Factory; seen with Polymarket Relayer and Synapse Bridge activity. Do not add to `INFRA_ADDRESSES`.
9.2 PolygonScan visible but unresolved
Address	Notes
`0xb4192b0521247ef964b88fa4bcba923866f49fd7`	Polymarket-labeled, 9 tx visible on label page. Needs signed-in fetch or lake scan.
`0x4cad9b4eaa0c0e64a204b07308e5adb345b523a0`	Polymarket-labeled, 9 tx visible on label page. Needs signed-in fetch or lake scan.
`0xeadb9b366d669afa3d43a60c49e28302aa2582b7`	Polymarket-labeled, 17 tx visible on label page. Needs signed-in fetch or lake scan.
9.3 Safe implementation metadata
Address	Notes
`0xe51abdf814f8854941b9fe8e3a4f65cab4e7a4a8`	GnosisSafeL2 singleton / implementation used by legacy Safe proxies. Track as Safe metadata. Do not add to `INFRA_ADDRESSES`.
---
FPMM / pre-2022 AMM era
Status: out of scope for the current lake.
The lake starts on `2022-11-21`, and no pre-CLOB activity exists in the current data window. Keep this section so a future historical backfill re-opens the issue.
Historical factory:
```text
0x8b9805a2f595b6705e74f7310829f2d299d21522  # Fixed Product Market Maker Factory
```
Why it matters for future backfills:
FPMM-era activity has a different PnL shape.
Liquidity providers have LP shares and inventory exposure, not simple buy/sell/split/merge/redeem flows.
Per-market FPMM pools are dynamically deployed, so a static `INFRA_ADDRESSES` list is not enough.
Future implementation options:
```text
1. Dynamic check: was the address created by the FPMM factory?
2. Enumerate FixedProductMarketMakerCreation events and load all pool addresses.
```
---
Current action list
Immediate code/config work
Confirm the live code already contains:
`0xada100874d00e3331d00f2007a9c336a65009718`,
`0xada200001000ef00d07553cee7006808f895c6f1`,
`0xf3cfb6a6ebfeb51876289eb235719eb1c65252b0`.
Add a dedicated `COLLATERAL_VAULTS_TO_SCAN` or equivalent config for:
`0xc417fd8e9661c0d2120b64a04bb3278c17e99db1`.
Run leg scan on `0xc417...` across USDC.e/native USDC wrap/unwrap flows.
Run activity/leg scan on `0x05cd...` before deciding whether it is Polymarket infra, third-party helper infra, or irrelevant to your lake.
Scan Router `0x1212...2600` for the 2026-06-23 ? present window.
Check whether native USDC is live after 2026-06-23; if yes, expand `COLLATERAL_TOKENS` beyond USDC.e and pUSD.
Keep as non-live / non-cash infra unless proven otherwise
Deposit Wallet Factory / Beacon / implementation,
Safe/proxy factories,
UMA adapters and oracle contracts,
implementation contracts behind proxies,
Combos/V2 modules with zero appearances in TASK8,
Perps unless overlap scan proves contamination.
---
Source notes
Primary source tiers:
First-party official deployment/security: Polymarket `contract-security`.
First-party product repo: Polymarket `ctf-exchange-v2` deployed contracts and source code.
Verified explorer/source: PolygonScan verified source/ABI and proxy metadata.
Internal evidence: lake scans, TASK reports, `custody_indexer.py`, and transaction-walk proofs.
Third-party maps: useful only as support, never as the sole promotion reason.
DefiLlama Polymarket fee adapter is used as supporting evidence for fee-recipient / reward-distributor classification; still scan watched transfer legs before changing PnL treatment.
Specific source notes used in this rework:
Polymarket `contract-security` lists all deployments on Polygon mainnet, including V1 contracts, Polymarket V2, Deposit Wallet, and Perps.
Polymarket `ctf-exchange-v2` lists Polygon deployments for the CollateralToken implementation/proxy, CollateralOnramp, CollateralOfframp, PermissionedRamp, current CtfCollateralAdapter, current NegRiskCtfCollateralAdapter, CTFExchangeV2, and NegRiskCtfExchangeV2.
`CollateralToken.sol` defines immutable `VAULT` as the address holding underlying assets; `wrap()` transfers the underlying asset to `VAULT`, and `unwrap()` transfers assets from `VAULT` to the recipient.
PolygonScan verifies `0x05cd9922a5d37fae921fc5dee280a9dbc4c3b393` as contract name `CtfAutoRedeem` with `redeemBinary` and `redeemNegRisk` functions plus `BinaryRedemption` and `NegRiskRedemption` events. It is not treated as official Polymarket AutoRedeemer unless separately proven.

**DEPLOY BLOCK 85,324,668 — MEASURED 2026-08-15** (`eth_getCode` binary search: code absent at 85,324,667, present, 25,626 chars, at 85,324,668). This closes a boundary that external research left as UNKNOWN with an upper bound of 85,820,694 (a first-use block, which must NOT be substituted for a deployment block). Related measured deploy blocks: pUSD `0xc011a7e1…` 84,902,320; AutoRedeemer `0xa1200000…` 87,479,597; V2 CtfCollateralAdapter gen1 `0xADa10087…` 84,902,341 / gen2 `0xAdA100Db…` 86,179,826; V2 NegRiskCtfCollateralAdapter gen1 `0xAdA20000…` 84,902,347 / gen2 `0xadA20056…` 86,179,892. ⚠️ **The two V2 generations are sequential deployments, not conflicting documentation — and the gen-1 ranges must NOT be closed at the gen-2 deploy blocks.** The old adapters stayed callable with no on-chain disable boundary; model them as overlapping open-ended ranges. See ADR-0044 §3 for the layer-precedence rule.
---
Change log
Date	Change
2026-08-20	Promoted BinaryModule `0x1000008dd9...00ba` and NegRiskModule `0x200000900045...8933` into `INFRA_ADDRESSES` (custody_indexer.py + infra_registry.json, 28 -> 30; gate roster 39 -> 41). Trigger: Phase A stopped 2026-06-26 on `_assert_bucket_claims_disjoint` — `combo` and `split_merge` both claimed tx `0x7a205a43...9161` ($7.35849), split_merge booking it to BinaryModule, which was in neither roster. MEASURED over 38 held custody/ days (2026-05-20 -> 06-26): both are exactly-balanced conduits (BinaryModule USDC.e in $19,125.51 / out $19,125.51 = $0.00 net), same signature as the two siblings promoted 2026-07-11. Plain exclusion loses no user money — receipt-verified, see the BinaryModule row. Router and PositionManager were deliberately NOT promoted (0 appearances as from/to; PositionManager IS COMBO_ERC1155); no implementation addresses were promoted (0 appearances each). Staged-day impact: 0 cash rows, 14 self-cancelling NegRiskModule holdings rows on 06-24/06-25 -> those 2 days restaged.
2026-08-11	Promoted the four remaining FEE_MODULE_CANDIDATES (`0xd4aa6f8e...` Fee Recipient/main, `0x525e4001...` Fee Recipient/NegRisk, `0xf21a25dd...` Fee Recipient/old, `0x2d507657...` ProtocolFeeWallet) into the ADR-0023 ledger-exclusion roster (`gate_no_infra_pnl.py`, both trees). Trigger: transfer-leg proof landed — MEASURED in the phaseB_combo_correction_20260810 export as trader rows: `0xd4aa6f8e` was the #1 wallet by total_pnl (+$37,734,774.93 / 304,484 rows / cost 0); the three present total +$41.34M. This satisfies the 2026-07-08 "registry-only unless a transfer-leg scan proves a concrete watched-leg role" condition. NOT added to `INFRA_ADDRESSES` (ADR-0014: wrong lever); the i2_net fee-term accounting is unchanged.
2026-07-01	Base completeness pass against current indexer and official docs.
2026-07-03	Cleaned formatting, normalized date style, removed messy raw pasted notes, and added flagged backlog candidates without promoting them to `INFRA_ADDRESSES`.
2026-07-04	Promoted `0xf3cfb6a6...52b0` BinaryModule redemption router and `0xada200001...c6f1` NegRiskCtfCollateralAdapter repo deployment to `INFRA_ADDRESSES`; label-pass triage folded in; file re-encoded UTF-8.
2026-07-05	External-review reconciliation confirmed `0xada100874...`, `0xada200001...`, and `0xf3cfb6a6...` are live in `custody_indexer.py`. Added/confirmed official contract-security coverage for FeeModule, Deposit Wallet implementation, Router, Perps, and module family.
2026-07-08	Added `0xe3f18acc...b7b0` as third FeeModule candidate (620f isolation evidence, ADR-0014); established FEE_MODULES-not-INFRA policy for the class.
2026-07-08	Patched fee/reward registry with missing direct fee recipients (`0xf21a25dd...`, `0xd4aa6f8e...`, `0x525e4001...`) and accounting/distribution endpoints (`0x3a9418...`, `0x2d5076...`, `0xc28848...`, `0xc53663...`). Preserved all old entries; no automatic promotion to `INFRA_ADDRESSES`.
2026-07-06	Reworked file structure; moved confirmed promoted addresses into live section; added `0xc417fd8e...99db1` pUSD backing vault; added `0x05cd9922...b393` CtfAutoRedeem candidate; added source-coverage section and cleaned PnL-action policy.

## SCREEN_DISCOVERED_UNAUDITED (added 2026-07-14, Campaign 19 Step 3 emitter census)

Discovered by Dune event-topic census over gate window blocks 83601585-88978536 (window 2026-03-01..2026-06-22).
NOT custody-excluded, NOT trusted infra. Promotion to canonical infra requires a completed role audit.
Evidence: /tmp/grokfix2/ledger/campaign19/emitter_triage.csv + EMITTER_TRIAGE_REPORT.md (volatile /tmp; census CSV emitter_census_vs_registry.csv).

address	codehash_sha3_256	topic_families(counts in window)	canonical contracts touched	suspected role	audit status
`0x1e50bf2bc715f022cb33f456269ded62011dded8`	`0x51df6776999af2cf…`	OrderFilledV2:18	0x4d97dcd97ec945f40cf65f87097ace5ea0476045	aux V2 exchange (OrderFilledV2)	UNAUDITED; first/last block per census pending
`0x269762f573f7d14034d7aaed0de30facea4f6b98`	`0x56d661a544b83d75…`	PositionsConverted(address,bytes32,uint256,uint256):1	0x4d97dcd97ec945f40cf65f87097ace5ea0476045	NegRisk-adapter-class (PositionsConverted)	UNAUDITED; first/last block per census pending
`0x546b3142b3711c7169485dd8bcd02817ccd2ae23`	`0x441e63fdfa50333c…`	Wrapped(address,address,address,uint256):162;Unwrapped(address,address,address,uint256):112	0x2791bca1f2de4661ed88a30c99a7a9449aa84174,0x4d97dcd97ec945f40cf65f87097ace5ea0476045	collateral ramp/wrapper (Wrapped/Unwrapped)	UNAUDITED; first/last block per census pending
`0x597ddfff5344c098e273e07fe93713602cff93e7`	`0xa220850fcdf03848…`	Wrapped(address,address,address,uint256):33;Unwrapped(address,address,address,uint256):20	0x2791bca1f2de4661ed88a30c99a7a9449aa84174	collateral ramp/wrapper (Wrapped/Unwrapped)	UNAUDITED; first/last block per census pending
`0x5bc2409366b2b205bb0abe5728ef30f15e4072d9`	`0xb4117b92659bd5c6…`	PositionsConverted(address,bytes32,uint256,uint256):2	0x4d97dcd97ec945f40cf65f87097ace5ea0476045	NegRisk-adapter-class (PositionsConverted)	UNAUDITED; first/last block per census pending
`0x93f0a57b6f7d1e765ca2674ab2ecb6ff6406b3c3`	`0xa4a076e90aa9e0b9…`	OrderFilledV2:338	0x2791bca1f2de4661ed88a30c99a7a9449aa84174,0x3a3bd7bb9528e159577f7c2e685cc81a765002e2,0x4d97dcd97ec945f40cf65f87097ace5ea0476045,0xd91e80cf2e7be2e162c6513ced06f1dd0da35296	**Legacy NegRisk Exchange V2** (Daniel 2026-07-14, HIGH conf: Polymarket Apr-2026 client config negRiskExchangeV2; replaced by 0xe222…). Settlement endpoint — exclude contract, attribute through to makers/takers/recipients	IDENTIFIED (Daniel 07-14); role audit for exclusion still required
`0xdbf75b4057ced0b6fc9b521acfabfe817613af04`	`0x5a8733380fec170a…`	OrderFilledV2:29	0x4d97dcd97ec945f40cf65f87097ace5ea0476045	aux V2 exchange (OrderFilledV2)	UNAUDITED; first/last block per census pending
`0xea9b93e6f4144318559f3fd8435ac67688d134fb`	`0x0f9fffde1a6b226c…`	BinaryRedemption(address,bytes32,uint256):1	0x2791bca1f2de4661ed88a30c99a7a9449aa84174,0x4d97dcd97ec945f40cf65f87097ace5ea0476045,0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb	redemption helper (BinaryRedemption, routes via CollateralOnramp+pUSD vault)	UNAUDITED; first/last block per census pending
`0xf60ca007115a47a11295f053156d913d83fed095`	`0x6025bc0c1ad74684…`	OrderFilledV2:132	0x4d97dcd97ec945f40cf65f87097ace5ea0476045	**Legacy standard CTF Exchange V2** (Daniel 2026-07-14, HIGH conf: official client config exchangeV2; replaced by 0xE111…). Infrastructure — look through every exchange leg to order participants	IDENTIFIED (Daniel 07-14); role audit for exclusion still required
`0xfeae6dfc09c79212ee0b8b8d511f58be2bc43f56`	`0xdbf9be39b59c3b98…`	Wrapped(address,address,address,uint256):11	0x2791bca1f2de4661ed88a30c99a7a9449aa84174	collateral ramp/wrapper (Wrapped)	UNAUDITED; first/last block per census pending

Third-party-classified census emitter, identified by Daniel 2026-07-14 (kept OUT of registry proper):
`0x051cdb38190b151f616bf2ea0e7e42bae712abc3` — verified pUSD-style CollateralToken minimal proxy ("Polymarket USD" ERC-20), NOT the canonical 0xC011… proxy; treat as non-canonical/retired until admin conclusively linked to Polymarket. Treatment: collapse matched USDC↔pUSD wrap/unwrap as neutral representation change (mint ≠ income, backing transfer ≠ expenditure); epoch-bound any exclusion. Confidence: HIGH function / MEDIUM provenance.

Daniel ruling 2026-07-14 on the remaining 10 UNRESOLVED rows (0x1e50, 0x2697, 0x546b, 0x597d, 0x5bc2, 0xdbf7, 0xea9b, 0xfeae, plus third-party 0x94b0, 0x9b1b): no exclusion without code/creation-provenance/transfer-leg proof; keep as unresolved actors/counterparties in the population; do not infer protocol ownership from proximity to known contracts. Deep audit delegated (see /tmp/grokfix2/ledger/campaign19/TASK_EMITTER_DEEP_AUDIT.md).

---

# 2026-07-22 — runtime-bytecode fingerprint of the 10 unresolved emitters (Claude, live eth_getCode)

Adds a NEW evidence axis to the 07-14 table above: exact runtime bytecode **length** and clone
matching against our identified contracts. Polymarket contracts embed addresses as Solidity
`immutable`s, so two deployments of the **same source** have **identical bytecode length but
different codehash**. "Same length, different hash" is therefore a strong same-source signal.
Method: `eth_getCode` on all 10 + our known references (drpc, 2026-07-22 ~22:00 UTC). All
lengths MEASURED; family/role = INFERENCE from length + emitted event. No PolygonScan source
verified yet — that is the open deep-audit (delegated to Fable, FABLE_PROMPT_7_IDENTIFY.md).

Reference lengths (known, identified): CTF-Exch-V2 `0xE1111800`=42076; NegRisk-Exch-V2
`0xe2222d`=42076; CTF-Exch-V1 `0x4bfb41`=34418; NegRisk-Exch-V1 `0xc5d563`=34550; NegRisk-Adapter
`0xd91e80`=34518; BinaryModule-router `0xf3cfb6`=27352; CollateralOnramp `0x930708`=5304;
pUSD-proxy `0xc011a7`=124 (codehash `c5731b62`); CtfAutoRedeem `0x05cd99`=25626.

| addr | emits | len | fingerprint verdict | reconciled identity | conf |
|---|---|---|---|---|---|
| `0xdbf75b40…af04` | OrderFilledV2 | 42076 | exact CTF-Exch-V2 length | CTF-Exchange-V2-class, different immutables (aux/secondary V2 exchange) | HIGH |
| `0x1e50bf2b…dded8` | OrderFilledV2 | 42186 | V2 +110 | Exchange-V2-family variant | MED |
| `0x93f0a57b…b3c3` | OrderFilledV2 | 30466 | byte-length twin of f60c | **same source as f60c** — an EARLIER exchange-V2 generation (30466-class), distinct from the current 42076-class. Consistent with 07-14 "Legacy NegRisk Exchange V2" (same ctf-exchange-v2 source, NegRisk immutables) | HIGH (pair) |
| `0xf60ca007…d095` | OrderFilledV2 | 30466 | byte-length twin of 93f0 | **same source as 93f0** — earlier standard exchange-V2 gen. 07-14 label "Legacy standard CTF Exchange V2, replaced by 0xE111" now REFINED: f60c is a predecessor generation, NOT the canonical 42076 contract. Label stands; generation clarified | HIGH (pair) |
| `0x5bc24093…72d9` | PositionsConverted | 34542 | ≈ NegRisk-Adapter 34518 / NegRisk-Exch-V1 34550 | NegRisk-adapter-class converter | HIGH |
| `0x269762f5…6b98` | PositionsConverted | 55618 | **no match to anything we hold — largest** | UNKNOWN. The one genuine unknown. Large NegRisk/combinatorial-class candidate; needs source | LOW |
| `0x546b3142…ae23` | Wrapped/Unwrapped (162/112) | 124 | codehash `c5731b62` = **identical to pUSD proxy** `0xc011a7`; EIP1967 impl → `0xd2c53c0a…` | pUSD-style CollateralToken PROXY (same proxy bytecode as canonical pUSD). Supports the clone-factory hypothesis | HIGH |
| `0x597ddfff…93e7` | Wrapped/Unwrapped (33/20) | 328 | proxy; EIP1967 impl → `0x7a390ef8…` | Wrapped-collateral / CollateralToken proxy, different impl generation | HIGH |
| `0xfeae6dfc…3f56` | Wrapped (11) | 10954 | standalone (near WCOL 9830) | Collateral-wrapper-class contract | MED |
| `0xea9b93e6…34fb` | BinaryRedemption (1) | 27352 | exact BinaryModule-router length (`0xf3cfb6`) | BinaryModule redemption-router sibling | HIGH |

**Net:** 7 of 10 placed into families we already index (3 exchange-class, 1 exchange variant,
1 NegRisk converter, 3 collateral-wrapper/CollateralToken, 1 BinaryModule redeemer). **One
genuine unknown: `0x269762f5…` (55618-len PositionsConverted emitter).** `0x1e50…` is MED.
Two proxy impls to verify: `0xd2c53c0a…` (behind 546b), `0x7a390ef8…` (behind 597d).

**Highest-leverage branch:** 546b's proxy bytecode is byte-identical to the canonical pUSD proxy.
CollateralToken is therefore a **cloned-proxy pattern** (cf. the 07-14 note on `0x051cdb38…`).
If so there is a factory minting wrapped-collateral tokens — a DYNAMIC child registry we cannot
enumerate by hand. Find the factory + creation event and enumerate the children. (FABLE_PROMPT_7.)

This CONFIRMS and REFINES the 07-14 rulings; it does not overturn them. Daniel's "no exclusion
without code/creation-provenance/transfer-leg proof" still governs — bytecode family is evidence
toward that proof, not the proof itself.

# 2026-07-22 — external registry audit (docs/campaigns/custody-pnl-cash-attribution/h2-attribution/fable/dump5, Fable) — graded findings

dump5 is a from-scratch infra registry trawl. **It is NOT a superset of ours.** Diff: 12 real
addresses it has and we lack; ~20 substantive addresses we have and it lacks (our entire
fee/reward/oracle/perps/wrapper surface + CtfAutoRedeem + pUSD backing vault). Graded findings:

- **NEW SYSTEM — an August-2023 NegRisk predecessor generation we are blind to.** Adapter
  `0xf16a3BdFFB7B882E3236243E901f6c5953E2EE0d` (creation tx `0xdc5f74ab…`, block 46,877,259,
  29 Aug 2023), predating the canonical `0xd91E80…` suite by 3 months, plus predecessor vault
  `0xd169Cf0E…`, operator `0xF09A3e19…`, UMA adapter `0xB5eaa4ad…`. dump5 admits production use
  is UNVERIFIED. **Cheaply testable from OUR lake** (custody covers Aug–Nov 2023): if the suite
  carried flow, `0xf16a…`/`0xd169…` appear as counterparties in our days. Zero rows ⇒ deployed-
  never-used, discard permanently. Non-zero ⇒ a 3-month window of unmapped conversions = a real
  defect. Decisive either way; queue it.
- **⚠ Operator address collision.** dump5 gives NegRiskOperator = `0x71523d0f147a9C5A14b74f9e2078F3575E6Fe2eD`
  **with a creation tx** (`0x30302f36…`, block 50,505,470). We hold `0x71523d0f655b41e805cec45b17163f528b59b820`
  for the same role (repo citation, no creation trace). Same `0x71523d0f` vanity prefix, different
  address. On evidence quality theirs is currently better-sourced. NOT wired in our code (so the
  conflict hasn't bitten). Resolve on-chain before either is adopted. (FABLE_PROMPT_5.)
- dump5's structural claims (independent of the address list): FPMM pools + deposit wallets must
  come from factory EVENTS not a static list; proxy implementations must be resolved AT THE
  TRANSACTION BLOCK (its stale-docs CombinatorialModule finding — official docs list `0xb529b2…`,
  live proxy resolves to `0x03CC063e…` deployed 16 Jul 2026 — is the proof case). AutoRedeem
  `beneficiary = event.from` (agrees with what we already hold; agreement ≠ corroboration).

# 2026-07-22 — registry wiring census (Claude)

Of 86 unique hex strings in this file (~6 are truncated topic hashes, not addresses): **~23 real
addresses are wired into the money path** (`custody_indexer.py` / `pnl_common.py` /
`rpc_log_fetcher.py`); 6 exist only in validators/gates (`validate_custody_pnl.py`,
`gate_no_infra_pnl.py` — the fee modules + `0x115f48dc…` skim: the indexer itself does not know
them); 17 are deliberately policy-excluded (identity/wallet layer + implementation contracts);
**28 are known-but-unwired**: 10 observed-in-our-data-unidentified (the table above), 8 fee/reward,
5 combos/router/perps, 5 protocol-infra-deferred (incl. the operator-collision address + NegRisk
vault). The `ADD_AT_REBUILD` / `SCAN_CASH_ENDPOINT` tags are promises against a rebuild that is
currently hard-blocked — that backlog is frozen, not progressing.

# 2026-07-23 — adapterid: 0x269 / 0x5bc resolved to LEGACY 4-arg PositionsConverted (MEASURED)

Supersedes the "one genuine unknown / LOW" line for `0x269762f5…` in the 07-22 fingerprint table
at the EVENT-SHAPE level. Method: raw `eth_getTransactionReceipt` on a real emit from each, +
independent keccak of both event signatures (Claude reproduced topic0 to the byte).

- topic0 `PositionsConverted(address,bytes32,uint256,uint256)`   = `0xb03d19dd…deb367e` (legacy, 4-arg, 32-byte data)
- topic0 `PositionsConverted(address,bytes32,uint256,uint256,uint256)` = `0x1b8b64a5…3022319` (pUSD, 5-arg, 64-byte data)

| addr | emit block | data bytes | verdict | dump7 guess |
|---|---|---|---|---|
| `0x269762f573f7d14034d7aaed0de30facea4f6b98` | 85606527 | 32 | **legacy 4-arg** NegRisk converter | WRONG (guessed pUSD) |
| `0x5bc2409366b2b205bb0abe5728ef30f15e4072d9` | 87930606 | 32 | **legacy 4-arg** NegRisk converter | RIGHT (legacy) |

Runtime (`eth_getCode`) cross-checks: 269 = 27,808 B, 5bc = 17,270 B; both contain the 4-arg
topic constant, neither the 5-arg. **Caveat:** legacy 4-arg *shape* ≠ the canonical adapter —
`0x269`'s 27,808 B ≠ canonical `0xd91e80` (17,258 B, W3(a)), so `0x269` is a distinct/larger
legacy-generation deployment; full identity+mechanics still owed (FABLE_PROMPT_8 group B).
Evidence: `docs/campaigns/custody-pnl-cash-attribution/h2-attribution/reports/REPORT_ADAPTERID.md`.

# 2026-07-23 — W3(a): canonical NegRiskAdapter convertPositions verified from source

`0xd91e80cf2e7be2e162c6513ced06f1dd0da35296` = verified `NegRiskAdapter` (Polygonscan, v0.8.19).
`convertPositions(marketId,indexSet,amount)` burns the SAME `_amount` for every selected NO —
verbatim `ctf.safeBatchTransferFrom(msg.sender, NO_TOKEN_BURN_ADDRESS, noPositionIds,
Helpers.values(noPositionIds.length, _amount), "")`. Getters MEASURED: `ctf()=0x4D97DCd9…`,
`col()=0x2791Bca1…` (USDC.e), `wcol()=0x3A3BD7bb…`, `NO_TOKEN_BURN_ADDRESS()=0xa5Ef39C3…`.
Selected-NO recipient in-lake is the burn address, not the adapter balance. This is the
contract basis for ADR-0025 (ACCEPTED). Evidence:
`docs/campaigns/custody-pnl-cash-attribution/h2-attribution/reports/REPORT_W3.md`.

# 2026-07-22/23 — dump7 (FABLE_PROMPT_7) graded: THIN, adopt almost nothing as fact

Candid but low-yield: obtained ZERO independent creation tx/block/deployer/verified-source for
any of the 10 emitters (its own §7 lists 7 non-verifications), and admits it treated our
bytecode measurements as inputs, not evidence — so agreement ≠ corroboration. Two keepers only:
(1) the CollateralToken **child-factory hypothesis is refuted from official source** — `CollateralToken`
(ctf-exchange-v2) has no child-deploying fn; pUSD was one proxy via generic `deployAndCall`, not
a factory-with-registry. The "enumerate wrapper children" lead is dead; replace with a
creation-trace scan from deployer `0xcA71EA69…`. (2) The 32-vs-64-byte PositionsConverted test it
named — now EXECUTED above (adapterid). Everything else (exchange variants, proxy impls, ea9b)
stays UNVERIFIED.

# 2026-07-23 — dump8 (FABLE_PROMPT_8, ran twice) graded: framework yes, identities NO

Independent per-address mechanics dossier for the 28 unwired addresses. Ran twice → cross-run
self-consistency audit. VERDICT: adopt the mechanics FRAMEWORK (6 accounting classes;
event-and-trace not address-label; fail-closed/quarantine unknown counterparties; wrap/unwrap
fee-free = representation change; AutoRedeem→source holder; V2 `FeeCharged` = fee/PnL separator)
— all SOURCED from public repos. Do NOT adopt per-address identity/dating: both runs lacked a
Polygon archive node, so those are UNVERIFIED/INFERENCE and every cross-run disagreement is in
that tier. Length-based standard-vs-NegRisk inference (e.g. 42076 vs 42186) is unreliable by
construction — use getters, not runtime length. Resolution of the disagreement set is the owed
ON-CHAIN BATCH (Safe-proxy check on 0x4cad/0xeadb first, then exchange getter discrimination,
0x115f/0xc288 fee-vs-reward traces, perps silo-intersection, operator-role history). Not yet run.

# 2026-07-23 — v3 combo/parlay family + current AutoRedeemer, VERIFIED source (dump10, Claude-graded)

dump10 (FABLE_PROMPT_10) had PolygonScan verified-source + live-tx access; Claude reproduced 5/5
event topic0s by keccak. These clear the `COMBOS_MODULE` backlog rows (TASK8 found 0 appearances in
the campaign tail; combos deployed 2026-05-26 block 87,479,409, so any presence is in the last ~4wk
of the custody window — verify via STRUCTAUDIT Audit 2). Deployer EOA = `Polymarket: Deployer 1`
`0xcA71EA69c54c163D17beB90BeB8D001E1Eb538A1`. ⚠ UUPS proxies — resolve impl AT the tx block
(CombinatorialModule + Router upgraded 2026-07-20 15:01:57); do not pin impl globally.

| component | proxy | current impl |
|---|---|---|
| PositionManager | `0x006F54F7f9A22e0000CC2AB60031000000ae9fEF` | `0x30c038F0Dae8dcC3E6AD51D016F50821D32Cb87e` |
| BinaryModule | `0x1000008dD9001B968442c1000017eaE6E0dA00Ba` | `0x492FEc596eC347459E1Ebe30b9245EB3B49B1BBa` |
| NegRiskModule | `0x200000900045e3B6259600682756002200028933` | `0xA61e7ca374F721D5b9FD5b0FEe6Fb90f27d448d7` |
| CombinatorialModule | `0x30000034706C7d8e12009DAB006Be20000c031A8` | `0x03CC063e6F9552E3842136538092134EdC8962DE` — 🔴 **CORRECTED 2026-08-20**: docs.polymarket.com/resources/contracts now lists `0xb529b2430d78868422C47934d9d61cC9D0C53dBb` as the current implementation (i.e. what this file already had at §4.5). Proxy unchanged. Moot for the roster — we key on proxies, and MEASURED over 38 held days NO implementation address of any Combos contract ever appears as a leg endpoint. |
| Exchange (V3/Combos) | `0xe3333700cA9d93003F00f0F71f8515005F6c00Aa` | `0x7345C6842b244926125ed4054905cAc49620B5dc` |
| Router | `0x12121212006e4CD160D18e3f00711DA5c3372600` | `0x6C405dA46fdC4172239E5053189b6577e290E62f` |
| AutoRedeemer | `0xa1200000d0002264C9a1698e001292D00E1b00af` | `0x64860bFD14fCcaAc09cd36f347784a9616AfB66C` |

**AutoRedeemer (F5 route — VERIFIED):** impl `0x64860bFD…` (`src/utils/AutoRedeemer.sol`, solc
0.8.34). `0x05cd9922…` CtfAutoRedeem is a SEPARATE standalone helper (solc 0.8.33), NOT this proxy's
impl — do not conflate. Redemption events + keccak-verified topic0:
- `Redemption(address from,uint256 positionId,uint256 payout)` = `0xeebddeddf4ae1ee54a48517af27958e7666d69c7ba2e3e7c2b0ff87ef5f4491e` — generic; **decode `conditionId = bytes31(positionId>>8)`, `outcomeIndex = uint8(positionId)`**; resolves to a COMBO condition ⟹ `combo_pooled`.
- `BinaryRedemption(address from,bytes32 conditionId,uint256 payout)` = `0xb434294b5904213c83a167af0068ab82637c6fd4fac945e2abc74ed8d3f4d52a`.
- `NegRiskRedemption(address from,bytes32 conditionId,uint256 payout)` = `0x74a51ebefec30281ec6849b727ec7916f9b1a3e5e148d6771d98315215b38b96`.
Beneficiary = `from` (holder whose position burns), NOT the ERC-20/onramp/wrapper endpoint (those
are plumbing). Batch calls emit one event per (holder,condition); attribute per-event.

**Combo cash treatment (SOURCED):** every combo cash op (split/merge/fill/compress/redeem/fee) is
ONE scalar collateral amount for the whole basket — NO per-condition cash vector exists on-chain.
Treat basket-level exact; quarantine atomic cash as `combo_pooled`; never inject into constituent
PnL. Constituent legs recoverable for TOPOLOGY only (`CombinatorialConditionPrepared(bytes31
conditionId, uint256[] legs)` topic0 `0x51c89d7e…`, `getLegs(conditionId)`). Bucket-3 class, same as
NegRisk convert.

# 2026-07-23 — dump11 (FABLE_PROMPT_11) address port + held-data fingerprint (Claude)

dump11 = the non-trade collateral-flow taxonomy (F2 income + wrap/unwrap + external transfers). It
carries two address sets: the **official collateral-infra** contracts (repo-sourced) and **12
reward/fee distributor candidates** (DefiLlama fee adapter `fees/polymarket.ts` @ commit `cd64d78`,
12-Jul-2026 — third-party classification, NOT Polymarket-official). Grading: dump11 is archive-blind,
so every identity here is a LEAD until it carries its own on-chain artifact (B2 gate). To close that,
each candidate was **fingerprinted against held `custody/` data** (Claude, 2026-07-23; tool
`ambig_scan.py` sibling `fingerprint.py`): per address, from/to skew + counterparty fan-out + event
mix over sampled days. **Universal discriminator: a user wallet MOVES ERC-1155 shares; every one of
the 12 has `share_evts = 0` — none trades.** Directional skew + fan-out then classifies distributor
(pays many, from-only) vs sink/collector (receives, to-only). **This footprint IS the on-chain
artifact the B2 gate requires** — it is custody-derived, not a label.

## Tier 1 — CONFIRMED non-user infra on held data (8/12). Wire into cash-exclusion at rebuild.
All collateral-only, zero share movement. sample = 2026-05-01/05-20 (+06-20 for taker); these are
**pUSD/V2-era** infra — zero footprint before ~May 2026 (V2 migration Apr-2026), consistent w/ dump11.

`0x3a9418b2651c8164db5ebc56f12008137865e0f7`	Maker-rebate distributor (dump11)	`REWARD_DISTRIBUTOR_CONFIRMED`	`F2_INCOME_INFRA`	FINGERPRINT 07-23: from=14,761→**12,049 distinct wallets**, to=2 (funding), 0 shares, ~$1.89M out≈in (pass-through). Was added 07-08 as fee/reward endpoint — now artifact-confirmed distributor. Match by ERC-20 log `from` (Disperse-routed). NOT INFRA holdings; F2 wallet-scope income, never condition PnL.
`0xc288480574783bd7615170660d71753378159c47`	Liquidity-reward distributor (dump11; PolygonScan "Reward Distributor 2")	`REWARD_DISTRIBUTOR_CONFIRMED`	`F2_INCOME_INFRA`	FINGERPRINT 07-23: from=6,170→**5,035 wallets**, to=0, 0 shares, $200k out. Corroborates §8d on-chain reward-distributor finding + the 07-08 registry add. F2 income.
`0xc536633ff12ee52e280b2af2594031060c5aaf41`	Holding-reward distributor (dump11)	`REWARD_DISTRIBUTOR_CONFIRMED`	`F2_INCOME_INFRA`	FINGERPRINT 07-23: from=130,651→**79,593 wallets** (textbook midnight batch), to=0, 0 shares, $23.5k out. Was added 07-08 — artifact-confirmed. F2 income.
`0x1510565e93c9729410b6e41088e014e312fd8829`	Referral-reward distributor (dump11)	`REWARD_DISTRIBUTOR_CONFIRMED`	`F2_INCOME_INFRA`	FINGERPRINT 07-23: from=3,025→**2,208 wallets**, to=0, 0 shares, $195k out. F2 income.
`0x520bf77d9d34c34a6a9723f50e1dcb887ed238c5`	Taker-rebate distributor (dump11)	`REWARD_DISTRIBUTOR_CONFIRMED`	`F2_INCOME_INFRA`	FINGERPRINT 07-23 (06-20 only, later-activating): from=2,893→**2,893 wallets** (one batch), to=0, 0 shares, $137.7k out. F2 income.
`0x2d507657ca4ebcc8f9a38f6764c07310b66dea54`	Protocol-fee wallet (dump11: "not a user-reward payer")	`FEE_SINK_CONFIRMED`	`FEE_MODULE_I2_ROSTER`	FINGERPRINT 07-23: from=0, to=3 (cp_in=2), **receives $1.46M, never pays**, 0 shares. Fee SINK, not distributor. Fee-class (i2_net roster era-bucketing applies), NOT a holdings infra endpoint.
`0x115f48dc2a731aa16251c6d6e1befc42f92accc9`	V2 fee recipient (already `I2_NET_ACTIVE`, §row 175)	`FEE_COLLECTOR_CONFIRMED`	`FEE_MODULE_I2_ROSTER`	FINGERPRINT 07-23: **to=4,185,678 receipts from 50,653 distinct traders**, from=4, 0 shares. Hard-corroborates the existing i2_net roster classification (pUSD-era skim). No change — evidence upgraded to fingerprint-grade.
`0xd152f549545093347a162dce210e7293f1452150`	Disperse batch helper (dump11)	`DISPERSE_HELPER_BENIGN`	`—`	FINGERPRINT 07-23: from=5/to=5, cp=1, 0 shares — near-absent in custody collateral (the distributor is the ERC-20 log `from`, Disperse is only the tx-level `to`). Do NOT allowlist Disperse as an ERC-20 counterparty; match the distributor `from` instead (dump11 rule). Not a trader; no cash-attribution role.

## Tier 2 — OUT-OF-WINDOW second-gen distributors (4/12). Zero custody footprint through 06-20.
dump11's "rotated/second-gen" arrays were added to the adapter 12-Jul-2026 — **after the custody lake
cutoff 2026-06-22** — so they physically cannot appear in-window (verified: 0 rows across 05-28→06-20).
**Zero PnL impact on the current rebuild.** Record as known; fingerprint on the next lake extension,
or one-shot RPC getter/creation-tx check if pre-wiring is wanted. Do NOT mark confirmed without an
artifact.
`0xfdb1b8dc7f5789a0c9a398026585b8b10fba5507`	Maker-rebate distributor gen-2 (dump11)	`SECOND_GEN_OUT_OF_WINDOW`
`0x2c2795ea295d5eb51f9121b728ed2ea4e936a709`	Liquidity-reward distributor gen-2 (dump11)	`SECOND_GEN_OUT_OF_WINDOW`
`0x607c8c9866ef3b4665c5a384188706be738d8bf8`	Holding-reward distributor gen-2 (dump11)	`SECOND_GEN_OUT_OF_WINDOW`
`0x8a80356b6304a08c24da30b0cf0d85b6907824ee`	Referral-reward distributor gen-2 (dump11)	`SECOND_GEN_OUT_OF_WINDOW`

## Tier 3 — OFFICIAL collateral-infra (repo-sourced, dump11 §2.2). CANDIDATE — verify before wiring.
These are from Polymarket's `ctf-exchange-v2` repo (pUSD wrapper stack + bridge route). Repo-sourced ≠
on-chain-confirmed-for-us; fingerprint (high-traffic ones resolve instantly) or a getter check before
promotion. Wrap/unwrap = ONE zero-PnL asset substitution (`Δpusd + Δunderlying = 0`); collapse legs,
never count both. Backing vault `0xc417fd8e…` is ALREADY promoted (line 94) — do not duplicate.
`0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb`	pUSD proxy	`COLLATERAL_WRAPPER_CANDIDATE`	Repo-official (dump11). ERC-20 collateral asset itself; endpoints are mint/burn (`0x0`) + wrap/unwrap. Verify then treat wrap/unwrap as pass-through, user-neutral (same class as vault).
`0x6bbcef9f7ef3b6c592c99e0f206a0de94ad0925f`	pUSD implementation	`COLLATERAL_WRAPPER_CANDIDATE`	Repo-official (dump11). Impl behind the proxy above.
`0x93070a847efef7f70739046a929d47a521f5b8ee`	CollateralOnramp (`wrap(asset,to,amount)` → `Wrapped`)	`RAMP_CANDIDATE`	Repo-official (dump11). Deposit/wrap plumbing; funding inflow endpoint, wallet-scope not condition.
`0x2957922eb93258b93368531d39facca3b4dc5854`	CollateralOfframp (`unwrap(asset,to,amount)` → `Unwrapped`)	`RAMP_CANDIDATE`	Repo-official (dump11). Withdrawal/unwrap plumbing; wallet-scope.
`0xebc2459ec962869ca4c0bd1e06368272732bcb08`	PermissionedRamp (EIP-712 witness wrap)	`RAMP_CANDIDATE`	Repo-official (dump11). Same wrap path, gated.
`0xc8a6871d4ec4dae64f605db0f8a0b3d9ef928d64`	fun.xyz Polygon mediator (`FunMediatedDeposit`)	`BRIDGE_ROUTE_CANDIDATE`	Explorer-derived (dump11), NOT repo-official — LOWER confidence. Bridge-deposit route; some upstream USDC.e solver legs have no Polymarket endpoint (external funding).
`0xf70da97812cb96acdf810712aa562db8dfa3dbef`	Relay-labelled solver (observed one deposit route)	`BRIDGE_ROUTE_CANDIDATE`	Explorer-label only (dump11), single observed tx — LOWEST confidence, "not established as the only solver". Do NOT wire on this evidence; lead only.

**Method note / reproducibility:** fingerprint = `custody/` scan filtered to the candidate set,
per-address from/to counts + `n_unique` counterparty fan-out + `erc1155` event count, sampled days
2026-{05-01,05-20,06-01,06-10,06-20},{2025 controls}. Memory-safe (filtered result tiny). All Tier-1
numbers are MEASURED; Tier-2 = MEASURED-absent (out of window); Tier-3 = repo-SOURCED, UNVERIFIED
locally. No address promoted to live `INFRA_ADDRESSES` by this note — this records evidence + the
wiring class; promotion is the rebuild-wiring step.
