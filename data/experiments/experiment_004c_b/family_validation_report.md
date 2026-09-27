# EXPERIMENT-004C-B — Family Validation Report

Status: frozen before 004C-B challenge empirical access.

Base SHA: `4e094d13b06d9e558cbc404cc4d472316d7d5bbb`.
Accepted DATA-001 identity SHA-256: `e89fe8e25dcc494dad3ed96fe6672ab9f247c01eb70d4c0270a913f18a200a72`.
Registry SHA-256: `499b22e760b511d11f81707058ed92ce6218bd11428d7dd319436de22811c4dc`.
Preregistration SHA-256: `d86df188d4f0dfdb0876b2a7b723750d27438f328f5c6a94100df714b5a80009`.

## Validation rule

A hard relationship is admitted only from contract wording, common event identity, settlement rules, and explicit mathematical logic. Price behaviour is never used to define membership or promote a relation.

The accepted 004A.2 exact-`Yes` canonical rule is binding. If a full partition contains any member that is not frozen usable in the claimed regime, the empirical subset is **not** renormalized to one. An observed subset of a proven exhaustive parent is only a mutually-exclusive nonexhaustive family with `sum(p_i) <= 1`.

## Mechanically proven parent families

- Peru presidential winner, event 106520: 49 outcomes, listed candidates plus another-candidate catch-all.
- Colombia presidential winner, event 34584: 28 outcomes, listed candidates plus Other; potential runoff included.
- Colombia runoff pair, event 481843: 5 outcomes consisting of three named pairs, Other, and 1st Round Outright Winner.
- Hungary TISZA seat-count brackets, event 263567: 8 exhaustive integer brackets.
- Hungary Fidesz-KDNP seat-count brackets, event 263762: 6 exhaustive integer brackets.
- Hungary TISZA national-list-vote brackets, event 266168: 5 exhaustive brackets with boundary-to-higher-bracket rule.
- Hungary Fidesz-KDNP national-list-vote brackets, event 266119: 5 exhaustive brackets with boundary-to-higher-bracket rule.
- Hungary TISZA at-least-seat thresholds, event 266366: nested monotonic constraints.
- Hungary Fidesz-KDNP at-least-seat thresholds, event 266367: nested monotonic constraints.

## Settlement-rule evidence

The frozen rule sources are recorded in `family_registry.json`. Key rule facts used for mechanical status are:

- Peru and Colombia presidential-winner events resolve to the listed winner, with a catch-all outcome covering an unlisted/otherwise unresolved winner state.
- Colombia's runoff-pair event explicitly resolves to one of the named pairs, `Other`, or `1st Round Outright Winner`.
- Hungary seat-bucket events resolve from one integer seat count and the published brackets cover the full domain.
- Hungary vote-share bucket events state that an exact boundary resolves to the higher bracket, eliminating bracket overlap ambiguity.
- Hungary at-least-seat events share one seat-count variable, making higher-threshold YES imply every lower-threshold YES.

## Empirical completeness result

No proven exhaustive parent family has all members 004A.2-usable in any primary PRE_ELECTION or ACTIVE_RESULTS scope.

Therefore **zero historical scopes may use a hard sum-to-one projection** under the frozen eligibility rules.

Eligible hard empirical scopes are partial mutually-exclusive subsets only:

- Peru presidential winner: 9/49 PRE and 22/49 ACTIVE in Peru first round; 5/49 PRE and 15/49 ACTIVE in Peru runoff.
- Colombia presidential winner: 4/28 PRE and 4/28 ACTIVE in Colombia first round; 2/28 PRE and 2/28 ACTIVE in Colombia runoff.
- Colombia runoff-pair family: 2/5 ACTIVE in Colombia first round only.

These scopes use only `sum(p_i) <= 1`. If the subset sum is already <=1, its structural residual is exactly zero.

The four Hungary bucket/threshold parent structures have zero frozen usable members in the primary 004A.2 lanes and are retained in the registry as proven-but-empirically-unavailable rather than dropped.

## Semantic competitive families

These groups are economically competitive but are **not** hard identities in this battery:

- Peru Senate most-seats event 106510;
- Peru Chamber most-seats event 106511;
- Hungary most-seats / second-most / third-most groups 106614 / 291004 / 291025;
- Colombia first-round winner group 34582;
- Hungary national-list-vote winner group 246787.

Tie and settlement semantics are not independently strong enough to promote these groups to sum-to-one identities. They may only enter the frozen leave-target-out soft-family test.

Frozen soft scopes with at least three usable members are:

- Peru first round ACTIVE: Senate 9 members, Chamber 9 members;
- Peru runoff ACTIVE: Senate 6 members;
- Colombia first round PRE: first-round-winner family 3 members.

## Unsupported staged relationship

First-round, runoff-pair and overall-winner markets are economically related, but this registry does not contain an exact candidate-level conditional identity without estimated conditionals. The proposed staged first-round-to-overall relationship is therefore `UNSUPPORTED` for hard structural inference and cannot rescue this battery.

## 004B reconciliation

004B reported 0 mechanical graph edges, 10 semantic/non-mechanical edges and 827 unverified edges. This registry does not rewrite those pairwise semantic edges. It adds independently proven **event-level hyperedges** from common contract settlement structure. Where the full hyperedge is not empirically usable, the projection fails closed to the weaker valid constraint or becomes unavailable.

No empirical 004C-B price outcome was used to create or repair these families.
