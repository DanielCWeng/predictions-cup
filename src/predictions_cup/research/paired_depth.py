# ruff: noqa: E501, I001
"""LANE B read-only paired-depth PAPER gate (#220).

Consumes *explicitly provenance-attested* JSONL full-book observations, not live APIs.
No trading actions or synthetic book repair. A PAPER discrepancy is NOT observed PnL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path
from typing import Any


class InvalidInput(ValueError):
    """Fail-closed provenance or book error."""


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise InvalidInput("timestamp missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise InvalidInput("timestamp must include timezone")
        return parsed.astimezone(UTC)
    except (ValueError, TypeError) as exc:
        raise InvalidInput("invalid timestamp") from exc


def _dec(value: Any) -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidInput("invalid decimal") from exc
    if not d.is_finite():
        raise InvalidInput("nonfinite decimal")
    return d


def _levels(raw: Any, reverse: bool) -> tuple[tuple[Decimal, Decimal], ...]:
    if not isinstance(raw, list):
        raise InvalidInput("complete book must contain level arrays")
    items: list[tuple[Decimal, Decimal]] = []
    for level in raw:
        if not isinstance(level, dict):
            raise InvalidInput("invalid level")
        price, size = _dec(level.get("price")), _dec(level.get("size", level.get("quantity")))
        if price <= 0 or price >= 1 or size <= 0:
            raise InvalidInput("invalid price or nonpositive depth")
        items.append((price, size))
    if len({p for p, _ in items}) != len(items):
        raise InvalidInput("duplicate price levels")
    if items != sorted(items, reverse=reverse):
        raise InvalidInput("depth is not price sorted")
    return tuple(items)


@dataclass(frozen=True)
class Frame:
    venue: str
    instrument: str
    received: datetime
    exchange_at: datetime
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    complete: bool
    connected: bool
    continuity: bool
    trusted: bool
    epoch: str
    revision: int | None
    previous_revision: int | None
    digest: str
    source: str

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> Frame:
        if raw.get("venue") not in ("SIG", "PM"):
            raise InvalidInput("unknown venue")
        for field in ("complete", "connected", "continuity", "trusted"):
            if type(raw.get(field)) is not bool:
                raise InvalidInput(f"missing explicit {field} attestation")
        instrument = str(raw.get("instrument_id", ""))
        if not instrument or instrument == "None":
            raise InvalidInput("missing exchange/token id")
        r = raw.get("revision")
        p = raw.get("previous_revision")
        if r is not None and (type(r) is not int or r < 0):
            raise InvalidInput("invalid revision")
        if p is not None and (type(p) is not int or p < 0):
            raise InvalidInput("invalid previous_revision")
        source = raw.get("source")
        if not isinstance(source, str) or not source:
            raise InvalidInput("missing source provenance")
        epoch = raw.get("connection_epoch")
        if epoch is None or not isinstance(epoch, (str, int)):
            raise InvalidInput("missing connection epoch")
        bids = _levels(raw.get("bids"), True)
        asks = _levels(raw.get("asks"), False)
        digest = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
        return cls(str(raw["venue"]), instrument, _time(raw.get("received_at")),
                   _time(raw.get("exchange_at")), bids, asks,
                   raw["complete"], raw["connected"], raw["continuity"],
                   raw["trusted"], str(epoch), r, p, digest, source)


@dataclass(frozen=True)
class Spec:
    exchange: str
    market: str
    token: str
    pm_market: str
    condition: str
    event: str | None
    direction: str
    mapping_sha256: str


def load_specs(mapping: Path, acceptance: Path, ids: Iterable[str]) -> dict[str, Spec]:
    requested = set(ids)
    if not requested:
        raise InvalidInput("explicit SIG exchange universe required")
    raw_bytes = mapping.read_bytes()
    doc = json.loads(raw_bytes)
    evidence = json.loads(acceptance.read_text(encoding="utf-8"))
    sha = hashlib.sha256(raw_bytes).hexdigest()
    if not evidence.get("all_records_verified") or (
        evidence.get("artifact_sha256", {}).get("crosswalk_json") != sha
    ):
        raise InvalidInput("MAPPING-001 artifact acceptance hash mismatch")
    specs: dict[str, Spec] = {}
    for row in doc.get("records", []):
        eid = row["sig_exchange_id"]
        if eid not in requested:
            continue
        pm = row.get("direct_polymarket")
        if (row.get("status") != "VERIFIED" or row.get("mapping_class") != "EXACT"
                or row.get("mapping_direction") not in ("SAME", "COMPLEMENT")
                or row.get("sig_outcome_label", "").upper() != "YES"
                or not isinstance(pm, dict)
                or len(pm.get("token_ids", [])) != 2
                or len(pm.get("outcomes", [])) != 2
                or pm.get("mapped_token_id") not in pm["token_ids"]
                or pm.get("mapped_outcome") not in pm["outcomes"]
                or pm["outcomes"][pm["token_ids"].index(pm["mapped_token_id"])] != pm["mapped_outcome"]):
            raise InvalidInput(f"unverified binary mapping: {eid}")
        specs[eid] = Spec(eid, row["sig_market_id"], pm["mapped_token_id"],
                          pm["market_id"], pm["condition_id"], pm.get("event_id"),
                          row["mapping_direction"], sha)
    if set(specs) != requested:
        raise InvalidInput(f"unaccepted exchanges: {sorted(requested-set(specs))}")
    return specs


def _invalid(frame: Frame | None, now: datetime, max_age_ms: int) -> str | None:
    if frame is None:
        return "MISSING_DEPTH"
    if not frame.complete:
        return "INCOMPLETE_SNAPSHOT"
    if not frame.connected:
        return "DISCONNECTED"
    if not frame.continuity:
        return "REVISION_OR_HEALTH_GAP"
    if not frame.trusted:
        return "UNTRUSTED"
    if frame.exchange_at > frame.received:
        return "SOURCE_CLOCK_AHEAD"
    if frame.received > now or frame.exchange_at > now:
        return "FUTURE_INFORMATION"
    if (now - frame.received).total_seconds() * 1000 > max_age_ms:
        return "STALE_RECEIPT"
    if (now - frame.exchange_at).total_seconds() * 1000 > max_age_ms:
        return "STALE_EXCHANGE"
    if not frame.bids or not frame.asks:
        return "ZERO_DEPTH"
    if frame.bids[0][0] >= frame.asks[0][0]:
        return "CROSSED_OR_LOCKED"
    return None


def _price_levels(levels: tuple[tuple[Decimal, Decimal], ...], complement: bool) -> tuple[tuple[Decimal, Decimal], ...]:
    return tuple((Decimal(1) - price, qty) for price, qty in levels) if complement else levels


def _take(levels: tuple[tuple[Decimal, Decimal], ...], qty: int) -> tuple[Decimal, Decimal]:
    remaining = Decimal(qty)
    cost = Decimal(0)
    marginal = Decimal(0)
    for price, available in levels:
        take = min(remaining, available)
        if take > 0:
            cost += price * take
            remaining -= take
            marginal = price
        if remaining == 0:
            break
    if remaining:
        raise InvalidInput("INSUFFICIENT_EXECUTABLE_DEPTH")
    return cost / Decimal(qty), marginal


def _str_levels(frame: Frame | None, side: str) -> list[dict[str, str]] | None:
    if frame is None:
        return None
    levels = frame.bids if side == "bids" else frame.asks
    return [{"price": str(p), "size": str(q)} for p, q in levels]


class PairedPaper:
    """Ordered append-only decision generator; never places or simulates observed fills."""

    def __init__(self, specs: dict[str, Spec], *, max_age_ms: int = 5000,
                 skew_ms: int = 2000, lot_cap: int = 20,
                 sig_fee_per_share: Decimal | None = None,
                 pm_fee_per_share: Decimal | None = None,
                 stress_per_share: Decimal | None = None) -> None:
        if max_age_ms <= 0 or skew_ms < 0 or lot_cap < 1:
            raise InvalidInput("invalid limits")
        self.specs = specs
        self.max_age_ms = max_age_ms
        self.skew_ms = skew_ms
        self.lot_cap = lot_cap
        self.costs = (sig_fee_per_share, pm_fee_per_share, stress_per_share)
        if any(c is not None and (not c.is_finite() or c < 0) for c in self.costs):
            raise InvalidInput("invalid cost assumptions")
        self.frames: dict[tuple[str, str], Frame] = {}
        self.last_time: datetime | None = None
        self.seen: set[str] = set()
        self.seq = 0

    def observe(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        self.seq += 1
        try:
            frame = Frame.parse(raw)
        except (InvalidInput, KeyError, TypeError) as exc:
            return [{"sequence": self.seq, "decision": "NO_TRADE",
                     "reason": f"INVALID_INPUT:{exc}", "raw_sha256":
                     hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()}]
        now = frame.received
        if self.last_time is not None and now < self.last_time:
            return [{"sequence": self.seq, "decision": "NO_TRADE", "reason": "OUT_OF_ORDER_RECEIPT",
                     "at": now.isoformat(), "instrument": frame.instrument}]
        self.last_time = now
        if frame.digest in self.seen:
            return [{"sequence": self.seq, "decision": "NO_TRADE", "reason": "DUPLICATE",
                     "at": now.isoformat(), "instrument": frame.instrument}]
        self.seen.add(frame.digest)
        key = (frame.venue, frame.instrument)
        previous = self.frames.get(key)
        if previous and frame.epoch == previous.epoch and frame.revision is not None and (
            frame.previous_revision is not None and frame.previous_revision != previous.revision
        ) and not frame.complete:
            frame = Frame(frame.venue, frame.instrument, frame.received, frame.exchange_at,
                          frame.bids, frame.asks, frame.complete, frame.connected, False,
                          frame.trusted, frame.epoch, frame.revision,
                          frame.previous_revision, frame.digest, frame.source)
        self.frames[key] = frame
        selected = [s for s in self.specs.values()
                    if (frame.venue == "SIG" and frame.instrument == s.exchange)
                    or (frame.venue == "PM" and frame.instrument == s.token)]
        return [self._row(s, side, now) for s in selected for side in ("BUY_YES", "BUY_NO")]

    def _row(self, spec: Spec, side: str, now: datetime) -> dict[str, Any]:
        sig = self.frames.get(("SIG", spec.exchange))
        pm = self.frames.get(("PM", spec.token))
        row: dict[str, Any] = {
            "schema_version": "lane-b-paper-v1", "label": "PAPER_NOT_OBSERVED_FILL",
            "decision_at": now.isoformat(), "sequence": self.seq,
            "sig_exchange_id": spec.exchange, "sig_market_id": spec.market,
            "pm_token_id": spec.token, "pm_market_id": spec.pm_market,
            "pm_condition_id": spec.condition, "pm_event_id": spec.event,
            "mapping_sha256": spec.mapping_sha256, "mapping_direction": spec.direction,
            "side": side, "decision": "NO_TRADE", "reason": None,
            "sig_depth": None if sig is None else {"bids": _str_levels(sig, "bids"), "asks": _str_levels(sig, "asks")},
            "pm_depth": None if pm is None else {"bids": _str_levels(pm, "bids"), "asks": _str_levels(pm, "asks")},
            "sig_exchange_at": None if sig is None else sig.exchange_at.isoformat(),
            "pm_exchange_at": None if pm is None else pm.exchange_at.isoformat(),
            "sig_received_at": None if sig is None else sig.received.isoformat(),
            "pm_received_at": None if pm is None else pm.received.isoformat(),
            "sig_source": None if sig is None else sig.source,
            "pm_source": None if pm is None else pm.source,
            "sig_epoch": None if sig is None else sig.epoch,
            "pm_epoch": None if pm is None else pm.epoch,
            "sig_revision": None if sig is None else sig.revision,
            "pm_revision": None if pm is None else pm.revision,
            "sig_trust": None if sig is None else sig.trusted and sig.connected and sig.continuity and sig.complete,
            "pm_trust": None if pm is None else pm.trusted and pm.connected and pm.continuity and pm.complete,
            "observed_fill_qty": None, "observed_pnl": None,
        }
        for name, book in (("SIG", sig), ("PM", pm)):
            reason = _invalid(book, now, self.max_age_ms)
            if reason:
                row["reason"] = f"{name}_{reason}"
                return row
        assert sig is not None and pm is not None
        if abs((sig.received - pm.received).total_seconds()) * 1000 > self.skew_ms:
            row["reason"] = "PAIR_SKEW"
            return row
        if any(c is None for c in self.costs):
            row["reason"] = "COSTS_UNKNOWN"
            return row
        sig_levels = sig.asks if side == "BUY_YES" else _price_levels(sig.bids, True)
        mapped_yes = (side == "BUY_YES") == (spec.direction == "SAME")
        pm_levels = pm.bids if mapped_yes else _price_levels(pm.asks, True)
        capacity = min(sum(q for _, q in sig_levels), sum(q for _, q in pm_levels),
                       Decimal(self.lot_cap))
        lots = int(capacity.to_integral_value(rounding=ROUND_FLOOR))
        if lots < 1:
            row["reason"] = "INSUFFICIENT_WHOLE_LOT_DEPTH"
            return row
        sig_vwap, sig_limit = _take(sig_levels, lots)
        pm_vwap, pm_limit = _take(pm_levels, lots)
        fees = sum((c for c in self.costs if c is not None), Decimal(0))
        row.update({
            "decision": "PAPER_COMPARABLE_NOT_EXECUTED",
            "reason": "UNOBSERVED_FILL_AND_UNCALIBRATED_QUEUE",
            "executable_whole_lots": lots,
            "sig_buy_vwap": str(sig_vwap), "sig_max_marginal_limit": str(sig_limit),
            "pm_reference_sell_vwap": str(pm_vwap), "pm_min_marginal_limit": str(pm_limit),
            "assumed_fees_slippage_fill_haircut_per_share": str(fees),
            "reference_discrepancy_after_assumptions_per_share": str(pm_vwap - sig_vwap - fees),
            "sig_cash_requirement_max": str(sig_limit * lots),
            "sig_gross_share_requirement": lots,
            "fill_probability": None,
            "pm_cross_venue_capital_comparable": False,
        })
        return row


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline, read-only LANE-B PAPER join; JSONL input must be forward-recorded")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--acceptance", type=Path, required=True)
    parser.add_argument("--exchange-ids", required=True)
    parser.add_argument("--sig-fee-per-share", type=str)
    parser.add_argument("--pm-fee-per-share", type=str)
    parser.add_argument("--stress-per-share", type=str)
    args = parser.parse_args()
    specs = load_specs(args.mapping, args.acceptance, args.exchange_ids.split(","))
    costs = [None if value is None else _dec(value) for value in (
        args.sig_fee_per_share, args.pm_fee_per_share, args.stress_per_share
    )]
    gate = PairedPaper(specs, sig_fee_per_share=costs[0],
                       pm_fee_per_share=costs[1], stress_per_share=costs[2])
    if args.output.exists() or args.output.resolve() == args.input.resolve():
        raise InvalidInput("output must be a NEW independent research file")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    # Exclusive-create: never overwrite capture, WAL, earlier PAPER or active output.
    with args.input.open(encoding="utf-8") as stream, args.output.open("x", encoding="utf-8") as out:
        for line in stream:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise InvalidInput("input must be JSON objects")
                rows = gate.observe(event)
            except (json.JSONDecodeError, InvalidInput) as exc:
                rows = [{"decision": "NO_TRADE", "reason": f"INVALID_RECORD:{exc}"}]
            for row in rows:
                counts[row["reason"] or "UNKNOWN"] = counts.get(row["reason"] or "UNKNOWN", 0) + 1
                out.write(json.dumps(row, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "rows_by_reason": counts,
                      "observed_fills": 0, "empirical_pnl": None,
                      "gate": "PAPER_ONLY_NO_PROMOTION"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
