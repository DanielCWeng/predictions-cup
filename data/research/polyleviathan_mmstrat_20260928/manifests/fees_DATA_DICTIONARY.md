# Data dictionary

## fees_<FAMILY>.parquet — one row per OrderFilled (order fill) = one participant order
Grain: each matched trade emits one OrderFilled per maker order plus one for the taker order (whose taker_address is the
exchange contract). So every participant in a match has its own row; `participant_address` is that order's owner.

| column | meaning |
|---|---|
| family, event_id, market_id, outcome_side | from the election inventory (outcome_side YES/NO/OTHER) |
| condition_id, token_id | market condition and outcome token |
| day, timestamp | UTC day; block timestamp (unix s) |
| tx_hash, log_index | the OrderFilled event id (unique with token_id) |
| participant_address | order owner (= source maker_address) |
| counterparty_address | source taker_address (exchange contract when this is the taker order) |
| order_is_match_taker_order | true when counterparty is an exchange contract, i.e. this order was the active/taker order |
| exchange_if_taker_is_exchange | V1_CTF / V1_NegRisk / V2_CTF / V2_NegRisk / ComboV3 |
| participant_side, price, size_shares, value_usd | source side of this order, price, shares, USDC notional |
| maker_address, taker_address | source fields kept verbatim for comparison |
| fee_evidence | custody_not_scanned_pre_fee_era / no_fee_leg_observed / fee_charged / fee_charged_partly_refunded / fee_charged_fully_refunded |
| fee_charged_usdc, fee_charged_shares | gross fee legs attributed to this fill (USDC, or outcome-token shares) |
| fee_refunded_usdc, fee_refunded_shares | FeeModule refunds attributed to this fill |
| fee_charged_usd_equiv, fee_refunded_usd_equiv, fee_net_usd_equiv | USDC + shares x fill price (derived) |
| n_charge_legs, n_refund_legs | counts of attributed legs |
| fee_asset | USDC_collateral and/or outcome_token |
| fee_contract | receiving / refunding fee contract(s) |
| fee_sent_by_exchange | a leg was sent by an exchange contract (vs directly by a wallet) |
| fee_leg_refs | tx_hash:log_index of every attributed custody leg (raw evidence) |
| attribution_rules | rule(s) used; *_AMBIGUOUS marks a non-unique choice |
| flag_multiple_fee_records | >1 charge leg on this fill |
| flag_attribution_ambiguous | any attribution on this fill was not unique |
| flag_fee_on_fee_disabled_market | fee charged while the snapshot says fees_enabled=false |
| flag_fee_on_non_taker_order | fee charged on a maker order (normal in V1, rare in V2) |
| fee_type, fee_rate, fee_taker_only, fee_rebate_rate, fees_enabled, fee_source, fee_category | CURRENT Gamma market settings (snapshot, not historical) |
| custody_scan | true / skipped_pre_fee_era / skipped_no_scoped_fills for that day |

## maker_rebate_payouts_scoped_wallets.parquet
wallet, utc_date, rebate_usdc, tx_hash, log_index, block_number — every distributor payout to a wallet that appears in these fills.

## unattributed_fee_legs.parquet
Raw custody legs in scoped txs that no rule could tie to a scoped fill (event_type, from/to, token, amounts, tx, log, block, leg_kind, fee_contract).

## polymarket_fee_regimes.csv / .parquet
regime_id, effective_from/to, scope, taker fee, maker rebate, carrier, contract, evidence.
