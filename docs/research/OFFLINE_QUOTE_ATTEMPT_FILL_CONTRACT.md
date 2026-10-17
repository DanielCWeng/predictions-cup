# OFFLINE-ONLY quote-attempt, fill identity and maker denominator contract

**Scope: inert synthetic research. NO issuer proof, PAPER/LIVE, real PnL, recording, trading or new data access.** #207 LIVE STOP-SHIP.

## Motivation and overlap boundary

Frozen 005G used 30-second decision bins, 60-second hypothetical quote lives, first touched BID and ASK separately; a single market print could satisfy overlapping shadow quotes. Its 100 historical DEV filled-side *equivalents* (sometimes scaled to fractions such as 67.75) are not 100 authenticated maker fills. The run replaces missing proxy \`fee_rate_bps\` with zero, which is NOT a source-certified native zero fee.

Frozen [#277](https://github.com/DanielCWeng/predictions-cup/pull/277) separately covers paired price/depth; this module does not reproduce or import its gate. Frozen [#304](https://github.com/DanielCWeng/predictions-cup/pull/304) emits \`MISSING_PROOF\`, \`locked_bundle=None\`, source_admitted/paper_admitted FALSE and locally tournament-scoped candidate hints. Frozen [#301](https://github.com/DanielCWeng/predictions-cup/pull/301) checks globally unique RAW native order and fill strings in its separate LockedBundle. No issuer-native global vs tournament/account/exchange namespace has been proven; **direct #304→#301 composition remains REJECTED**, not silently repaired or mapped. This schema does not construct a LockedBundle.

## Future operator-exported object: \`offline-maker-attempts-v1\`

A synthetic \`source\` object **must preserve** \`permission_ref\`, \`issuer_id\`, \`capture_epoch\`, \`publisher_clock_basis\`, \`receive_clock_basis\`, optional original \`source_signature\` and \`original_sha256\`. The \`native_id_namespace_status\` is exactly \`UNKNOWN\`; changing a self-declared JSON flag to VERIFIED_GLOBAL is rejected. These are claims not authenticated rights, issuer signatures, clock attestations or hash custody.

An ordered \`wire_attempts\` collection must preserve **every** proposed quote attempt, not just ACKs, fills, positives or closes. Each row has \`tournament_id\`, \`exchange_id\`, \`market_id\`, \`wire_attempt_id\`, \`decision_id\`, \`logical_intent_id\`, \`idempotency_key\`, local \`MAKE/TAKE/UNKNOWN\` label separately from asserted \`MAKER/TAKER/UNKNOWN\` native role, BID/ASK and YES/NO, original quote \`limit_price\`, positive integer-valued decimal-string \`whole_size\`, \`quote_revision\`, non-certified \`queue_assumption\`, \`exposure_reservation\` and \`publish_state\` (ON_WIRE, POSSIBLY_ON_WIRE, NOT_ON_WIRE). Preserve \`decision_at\`, \`publish_at\`, \`ack_at\`, \`cancel_request_at\`, \`cancel_ack_at\`, \`terminal_at\` independently as explicit UTC or NULL, plus \`terminal_state\` OPEN, UNCERTAIN, CANCELLED, EXPIRED, REJECTED or FILLED. Preserve both raw \`native_order_id\` and the tuple \`(tournament, exchange, issuer, native_order_id)\` as a **non-authoritative scoped hint**.

Each nested \`events\` item records \`journal_event_id\`, \`kind\` (PUBLISH, ACK, FILL, CANCEL_REQUEST, CANCEL_ACK, REJECT, TERMINAL), \`issuer_at\`, \`received_at\` and \`publisher_signature\` as original claims. Every FILL needs distinct \`native_fill_id\` **and** \`native_trade_id\`, positive whole \`whole_qty\`, \`fill_price\`, claimed \`issuer_liquidity_role\`, separately nullable original \`fee_paid\`, \`rebate_paid\`, \`fee_currency\`. Fee \`null\` means UNKNOWN, never 0. Local MAKE does not certify issuer maker.

## Denominator, identity and censored-state rules

- \`total_wire_attempts\` counts keyed wire attempts including retries; distinct wire IDs sharing idempotency are separate attempts. Duplicate \`(tournament,exchange,wire_attempt_id)\` is invalid. \`local_make_attempt_denominator\` counts every **MAKE-labelled** wire attempt regardless of ACK loss, terminal rejection, zero fill, cancellation or uncertain reservation. It is **NOT** a native maker-eligible order denominator. TAKE or UNKNOWN cannot inflate MAKE.
- An explicit zero-fill local quote requires a terminal CANCELLED/REJECTED/EXPIRED clock and zero distinct claimed FILL events. OPEN/UNCERTAIN, missing ACK and CANCEL_REQUEST without CANCEL_ACK stay censored; exposure cannot be certified released on a cancel request. Late fill after cancel ACK is **retained** and flagged.
- Identical journal event replays are consumed once. Same ID with mutated payload fails, as does one event attached across distinct wire attempts. Same native fill ID reused across quote revisions/windows, or same trade ID spent by BID/ASK or two 30s grid quotes within a 60s life is flagged. No claim can be used twice. If original native fill/trade ID is absent return \`FILL_IDENTITY_UNKNOWN\`; **never** infer it from a quote/timestamp/price hash.
- Distinct tournaments with the same RAW ID keep both raw IDs and different scoped hints, but native issuer scope is still UNKNOWN. No unsupported issuer namespace policy is chosen. The future locked maker estimator's raw uniqueness constraint remains a separate default-deny requirement.
- Source rights, authentic publisher/source clock and epoch, original maker role, queue priority, fills/fees/terminal cash and native completeness are not proven by validation. For **every** possible input, \`source_admitted=false\`, \`paper_admitted=false\`, \`native_fill_count=null\`, \`native_fill_rate=null\`, \`realised_after_cost_net=null\`. No simulated numerator, maker allocation or fee claims.

## Generated adversarial matrix (18 isolated synthetic tests)

| Case | Required result |
| --- | --- |
| ACK-zero fill plus REJECT | Two local MAKE attempts in denominator; two explicitly zero-filled; no native maker rate |
| ACK possibly lost, CANCEL_REQUEST but no CANCEL_ACK, released reservation | \`ACK_MISSING_OR_POSSIBLY_LOST\`, \`CANCEL_UNCERTAIN\`, \`TERMINAL_UNRESOLVED\`, \`EXPOSURE_RELEASE_UNPROVEN\` |
| 30-second grid quotes t=0 and t=30; same trade at t=45 | \`TRADE_EVENT_DOUBLE_SPENT_ACROSS_QUOTES\` |
| BID and ASK claim same native fill | \`NATIVE_FILL_DOUBLE_CONSUMED\` |
| Two attempt fills share fill and trade IDs | Both duplicate reasons; no native numerator |
| Exactly repeated journal FILL event | One claim, not two |
| Mutated repeat of journal event ID | \`JOURNAL_EVENT_CONFLICT\` |
| CANCEL_ACK followed by late FILL | \`FILL_AFTER_CANCEL_ACK_UNRECONCILED\`; retain evidence |
| Different fake tournaments reuse raw order, fill, trade | Separate local scoped hints; \`NATIVE_ID_NAMESPACE_UNKNOWN\` |
| JSON claims VERIFIED_GLOBAL native-ID namespace | \`UNVERIFIED_NATIVE_NAMESPACE_CLAIM\` |
| Missing trade ID / maker role / fee | \`FILL_IDENTITY_UNKNOWN\`, \`ISSUER_MAKER_ROLE_UNVERIFIED\`, \`FEE_OR_REBATE_UNKNOWN\` |
| OPEN and no terminal | Censored, not certified zero fill |
| Fractional native size \`67.75\` | \`NOT_POSITIVE_WHOLE_SIZE\` |
| Native claimed FILL size exceeds posted whole size | \`CLAIMED_FILL_EXCEEDS_WHOLE_SIZE\` |
| Missing issuer or receive clock | \`ISSUER_OR_RECEIVE_CLOCK_UNKNOWN\` |
| Simultaneous MAKE and TAKE labels | TAKE excluded from local MAKE denominator |
| Duplicate JSON fields or NaN | Parser rejects rather than silently accepting |
| Offline import audit | No runtime/trading/network imports |

## Future proof boundary — NOT collected here

Only an owner/issuer-authorised, independently attested immutable private capture of **all quote decisions and possible on-wire attempts**, per-wire pre-POST durable observer events, unique issuer native orders/fills with an **authenticated namespace**, complete ACK/reject/cancel/late-fill terminality, native trade prints vs fill allocations, signed maker/taker role, paid fee/rebate currency, original whole-size level depth, YES/NO mapping, publisher+receive clocks/seq/epoch, exposure reservation, settlement/void/recount/closed cash and independent no-trade/unchanged-control event/day uncertainty could support a future cash-alpha evaluation. This is a specification of missing evidence **not permission to capture it**. #84 nine functional LIVE fail, #264 six strict XFAIL including X3–X6 and #220 performance remain separate release blockers. No direct collector/maker import, REST/WS, host/WAL/journal use, orders, service/config/risk changes or merges.
