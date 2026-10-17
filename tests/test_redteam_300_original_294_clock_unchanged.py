"""Independent frozen #294 005I naive issuer-clock strict replay (invented only).
The original negative test function body/assertions below is byte-identical.
No original source admission or execution economics claim.
"""
from __future__ import annotations

from dataclasses import replace

from test_005i_after_cost_admission_oracle import evaluate_invented_trial, invented_trial


def test_005i_naive_issuer_publisher_clock_must_fail_closed() -> None:
    """Malformed source timestamps should reject, not raise or score profit."""
    t = invented_trial()
    publisher_without_timezone = t.arrival.published_at.replace(tzinfo=None)
    arr = replace(t.arrival, published_at=publisher_without_timezone)
    result = evaluate_invented_trial(replace(t, arrival=arr))
    assert result.status == "NET_NOT_ESTIMABLE"
    assert result.realised_net_pnl is None
