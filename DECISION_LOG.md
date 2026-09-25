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
