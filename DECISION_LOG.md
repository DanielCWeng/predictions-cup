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
