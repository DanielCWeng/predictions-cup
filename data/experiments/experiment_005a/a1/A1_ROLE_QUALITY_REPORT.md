# EXPERIMENT-005A A1 — Role Identification Quality

**Scope:** outcome-independent role-quality audit only.
**Gate 2:** CLOSED; no price-response/challenge evaluation was performed.
**DATA-001 manifest SHA-256:** e3d95735262b0be3e9fe7dd32fb54e53dc7e65b403ef9b5a2261ac107a8ea4c4
**DATA-002 manifest SHA-256:** 3bcb544fdcf3479f5e8a6973906c8ccfd5b9abd77592629daa70facfdfdd6d5c

## Exact DATA-001 ↔ DATA-002 identity join

Across the ten frozen PRE_ELECTION / ACTIVE_RESULTS event-regime windows:

- DATA-001 fills: **374,891**
- exact DATA-002 matches: **374,891 / 374,891 (100.000%)**
- unmatched DATA-001 fills: **0**
- duplicate DATA-002 right-side join keys: **0**
- outcome disagreements: **0**
- price disagreements: **0**
- known infrastructure addresses occupying the signed-order-owner slot: **0**
- unattributed DATA-002 fee legs intersecting these event/token windows: **0**

The exact key is transaction_hash/log_index/token_id/maker_address against
tx_hash/log_index/token_id/participant_address. This is an identity join, not a
timestamp/price nearest-neighbour match.

## Transaction-condition structural consistency
Every observed transaction-condition group has **exactly one active order owner** and at least one
passive owner. There are no zero-active or multi-active groups in the ten frozen windows.

Multiple passive owners are common and remain explicit; transaction grouping must not be simplified
to a two-row assumption.

## V2 agreement

The six post-V2 event-regime windows contain **94,962** joined fills.

| Order-role / fee cell | Rows |
|---|---:|
| active/taker order + positive realised fee | 35,049 |
| active/taker order + no positive realised fee | 270 |
| passive/non-taker order + positive realised fee | 235 |
| passive/non-taker order + no positive realised fee | 59,408 |

Therefore:

- positive-fee coverage of active/taker-order rows: **99.236%**
- positive-fee incidence on passive/non-taker-order rows: **0.394%**
- clean role/fee agreement: **99.468%**
- passive-order positive-fee contradiction incidence: **0.247% of all post-V2 rows**
- high-confidence maker+taker coverage under the frozen conservative policy: **99.468%**

The 505 disagreement/supportive rows are not repaired or dropped silently.

## Complement-pair attribution diagnostic
There are **235** post-V2 passive-order rows carrying a positive fee in the frozen event windows.

A strict, outcome-independent complement check explains **119 / 235 (50.64%)** of them with a
unique active-order row in the same transaction and condition where:

- the passive row's counterparty is the active row's signed owner;
- outcomes are complementary;
- prices sum to exactly one within tolerance;
- share sizes match;
- the active row itself has no positive fee.

This pattern is consistent with adjacent-log fee attribution binding the fee transfer to the wrong
member of a complementary matched-order pair. It is an explanation diagnostic only. The frozen
005A role policy continues to label these contradictory passive-fee rows AMBIGUOUS.

## Frozen role-class counts

Across all ten event-regime windows:

- TAKER_HIGH_CONFIDENCE: **35,049**
- MAKER_HIGH_CONFIDENCE: **59,408**
- TAKER_SUPPORTIVE: **109,792**
- MAKER_SUPPORTIVE: **170,407**
- AMBIGUOUS: **235**

The supportive rows are concentrated in the pre-V2 windows, where the experiment intentionally
does not upgrade zero-fee rows to fee-confirmed role labels.

## A1 disposition

**STRONG ROLE-ANNOTATION QUALITY POST-V2.**

This is not an alpha result. It establishes that the newly observable active/passive role variable
is sufficiently coherent to support a V2-first discovery experiment while preserving a small,
auditable uncertainty tail. The primary 005A response surfaces should therefore use the frozen
high-confidence V2 subset, with supportive/pre-V2 rows reserved for robustness and coverage only.
