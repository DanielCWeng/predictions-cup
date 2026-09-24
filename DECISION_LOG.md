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

**Decision:** Canonical records representing instants reject naive datetimes and normalise accepted timestamps to UTC.

## DECISION 007 — Opaque external identifiers

**Decision:** Platform identifiers remain non-blank strings and acquire no numeric/business semantics from their formatting.

## DECISION 008 — Canonical models are separate from transport payloads

**Decision:** Remote API DTOs/adapters may be added later but must convert into the canonical domain contracts rather than redefining them.

## DECISION 009 — Trading configuration fails closed

**Decision:** Trading defaults disabled. Enabling the configuration flag requires a separately supplied trade credential but still creates no execution capability.

## DECISION 010 — Read/trade credential separation

**Decision:** Configuration represents read-capable and trade-capable credentials independently to support least privilege where SIG-issued key scopes allow it.
