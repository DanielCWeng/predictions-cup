# Decision Log

## DECISION 001 — Modular monolith / one Python service initially

**Decision:** Start with one Python service and logical modules inside one package.

**Reason:** Short competition window and low demonstrated need for distributed architecture.

## DECISION 002 — REST as authoritative truth

**Decision:** REST will ultimately be authoritative for financial/state truth. Realtime will be treated as acceleration/invalidation and reconciled against authoritative state.

## DECISION 003 — Central Risk mediates execution

**Decision:** Strategies will eventually propose trades but will not directly execute them. Central Risk will mediate execution.

## DECISION 004 — No production trading at bootstrap

**Decision:** No production trading capability exists at repository bootstrap.

## DECISION 005 — Decimal canonical numerics

**Decision:** Canonical financial/probability/order numeric values use `Decimal`; binary floats are rejected at canonical model boundaries.

## DECISION 006 — Timezone-aware UTC timestamps

**Decision:** Canonical records representing instants accept only actual timezone-aware `datetime` objects and normalise them to UTC. Wire-format strings/epochs must be parsed by transport adapters before entering the canonical layer.

## DECISION 007 — Opaque external identifiers

**Decision:** Platform identifiers remain non-blank strings and acquire no numeric/business semantics from their formatting.

## DECISION 008 — Canonical models are separate from transport payloads

**Decision:** Remote API DTOs/adapters may be added later but must convert into the canonical domain contracts rather than redefining them.

## DECISION 009 — Trading configuration fails closed

**Decision:** Trading defaults disabled. Enabling the configuration flag requires a separately supplied trade credential but still creates no execution capability.

## DECISION 010 — Read/trade credential separation

**Decision:** Configuration represents read-capable and trade-capable credentials independently to support least privilege where SIG-issued key scopes allow it.

## DECISION 011 — Stable canonical order kinds

**Decision:** `OrderKind` is restricted to `MARKET` and `LIMIT`. LIMIT requires a limit price; MARKET forbids one.

## DECISION 012 — Position means platform holdings

**Decision:** Canonical `Position.quantity` represents non-negative outcome shares held on an Exchange. Signed directional/risk exposure must be derived in later risk/state models rather than encoded as negative platform holdings.

## DECISION 013 — SIG read transport is separate from canonical models

**Decision:** Validate SIG REST payloads in `predictions_cup.sig` transport DTOs and convert explicitly into BUILD-002 canonical models only where semantics are lossless.

**Reason:** The OpenAPI payloads contain API-specific context, coverage, spread and node metadata and nullable fields that should not distort domain contracts.

## DECISION 014 — Tournament context remains explicit in BUILD-003

**Decision:** Context-sensitive SIG read methods accept caller-supplied `tournament_id`; the client does not automatically inject configured tournament values or select from organization-wide discovery contexts.

**Reason:** The API has endpoint/key-dependent omission semantics, and silently choosing a tournament can read the wrong isolated market state.

## DECISION 015 — Bounded GET retries only for documented transient failures

**Decision:** Read-only transport retries are bounded with exponential backoff and jitter. Response retries are limited to `429 RATE_LIMITED`, `503 TX_CONFLICT`, and `503 SERVICE_UNAVAILABLE`; transport failures/timeouts are also bounded because GET is non-mutating.

**Reason:** Retrying permanent client/auth/not-found errors or arbitrary server failures hides defects and can amplify incidents.


## DECISION 016 — Tournament-wide Realtime does not imply tournament-wide resident depth

**Decision:** Continue tournament-level Realtime capture for every known exchange, but maintain authoritative resident full depth only for an explicitly tracked subset. Untracked exchanges remain visible and retain Realtime/scalar observations without claiming full-depth trust.

**Reason:** The live tournament exposed 237 open exchanges. Maintaining every full book inside the prior 30-second fallback is incompatible with a conservative observed REST operating envelope and would consume capacity needed for recovery and future trading-critical reads.

## DECISION 017 — Broad SIG state uses bulk scalar observations

**Decision:** Use the existing GET /exchanges/prices transport in batches of at most 100 IDs for broad latest-price/BBO state. Bulk scalar observations never create or upgrade full-depth trust.

**Reason:** The supplied contract exposes a compact authoritative scalar surface for broad monitoring. At the observed 237-exchange universe it requires three requests rather than 237 full-book requests.

## DECISION 018 — Live SIG REST shares one priority governor

**Decision:** All REST attempts made by the explicit live SIG capture path share one per-client governor with HIGH, NORMAL and BACKGROUND priority, pacing, shared 429 cooldown and observable counters. The initial configured rate is 2 requests/second.

**Reason:** Rate limiting is documented per API key, so independently paced workers can collectively overload the same key. The original blocking curl + sleep probe only demonstrated roughly 2.0–2.4 request starts/second; it did not validate 3 requests/second. The 2 requests/second default is therefore deliberately conservative until a fixed-cadence live probe establishes a higher sustainable rate. It remains project deployment configuration, not a published SIG venue limit.

## DECISION 019 — Thirty-second depth freshness is a project expiry-safety policy

**Decision:** Apply the configurable full-depth freshness fallback only to tracked open books. When a tracked book crosses the bound, remove trust before awaiting its authoritative refresh.

**Reason:** SIG documents that order expiry emits no Realtime event, while the documented aggregate exchange orderbook does not expose per-order expirationDate. The 30-second default is therefore our conservative fallback, not a SIG contractual requirement. The Realtime prose's reference to GET /markets/{id}/orders is also inconsistent with the participant OpenAPI paths, so no undocumented endpoint is invented.


## DECISION 020 — Bulk missing IDs invalidate resident scalar state

**Decision:** When GET /exchanges/prices reports an exchange in missingIds, clear that exchange's resident scalar latest-price, best-bid, best-ask, spread and scalar observation timestamp.

**Reason:** Retaining the previous scalar values would let an untracked or depth-untrusted exchange expose stale BBO through the runtime fallback path. Missing authoritative scalar coverage therefore fails closed rather than preserving stale state.
