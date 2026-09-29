"""No-I/O recorder adapter for MAKE's live SIG state engine.

BUILD-004 state reconciliation emits recorder callbacks for CAPTURE provenance. MAKE
must not depend on analytics persistence, so the production maker supplies this
explicit sink instead of opening SQLite. Execution durability still uses the
BUILD-009 execution journal in LIVE mode.
"""

from __future__ import annotations


class NoopSigRealtimeRecorder:
    """Accept SIG state-engine recorder callbacks without performing I/O."""

    def record_delivery(self, **fields: object) -> None:
        del fields

    def record_trade(self, **fields: object) -> None:
        del fields

    def record_book_dirty(self, **fields: object) -> None:
        del fields

    def record_market_settled(self, **fields: object) -> None:
        del fields

    def record_market(self, **fields: object) -> None:
        del fields

    def record_prices(self, **fields: object) -> None:
        del fields

    def record_missing_prices(self, **fields: object) -> None:
        del fields

    def record_book(self, **fields: object) -> None:
        del fields

    def record_transition(self, **fields: object) -> None:
        del fields
