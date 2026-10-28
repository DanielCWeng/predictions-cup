from dataclasses import replace
from types import SimpleNamespace

from predictions_cup.maker.depth_priority import CandidateDepthQueue
from predictions_cup.runtime.models import OrderAction
from test_residual_taker_live import _Harness, _snapshot, TOKEN


def test_prioritises_largest_positive_after_cost_edge():
    queue = CandidateDepthQueue(3)
    assert queue.offer("weak", 0.10, 100)
    assert queue.offer("strong", 2.0, 200)
    assert queue.offer("middle", 1.0, 300)
    assert queue.pick(400).exchange_id == "strong"
    assert queue.pick(400) is None
    assert queue.pick(2_000_000_500) is None


def test_priority_queue_expiry_and_bound():
    q = CandidateDepthQueue(2)
    assert q.offer("a", 0.2, 10)
    assert q.offer("b", 0.4, 20)
    assert not q.offer("worse", 0.01, 21)
    assert q.offer("better", 0.9, 22)
    assert set(q.pending) == {"b", "better"}
    assert q.pick(1_300_000_000) is None
    assert q.dropped_expired == 2


def test_priority_coalesces_and_does_not_starve_other_markets():
    q = CandidateDepthQueue()
    assert q.offer("a", 0.1, 500)
    assert q.offer("a", 4.0, 600)
    assert len(q.pending) == 1
    assert q.pick(700).score == 4.0
    assert q.offer("a", 5.0, 2_100_000_000)
    assert q.offer("b", 2.0, 2_100_000_000)
    assert q.pick(2_100_000_001).exchange_id == "b"
    assert q.pick(3_000_000_000) is None
    assert q.pick(5_500_000_000) is None


def test_invalid_scores_rejected_without_queue_growth():
    q = CandidateDepthQueue()
    for score in [0, -1, float("nan"), float("inf")]:
        assert not q.offer("bad", score, 100)
    assert not q.pending


def test_precheck_only_spends_rest_for_fresh_conservative_positive_bound():
    h = _Harness(economics_gate_enabled=True)
    snap = _snapshot(ask=140, pm_bid=0.75, pm_ask=0.76)
    signal = SimpleNamespace(direction="BUY", quantity=50)
    expected = h.coordinator._candidate_refresh_score(snap, signal, action=OrderAction.BUY)
    assert expected is not None and expected > 0
    unattractive = _snapshot(ask=140, pm_bid=0.705, pm_ask=0.76)
    assert h.coordinator._candidate_refresh_score(
        unattractive, signal, action=OrderAction.BUY,
    ) is None
    stale = replace(snap, external_quotes={
        TOKEN: replace(snap.external_quotes[TOKEN], observed_monotonic_ns=1)
    })
    assert h.coordinator._candidate_refresh_score(
        stale, signal, action=OrderAction.BUY,
    ) is None
    bad_bbo = replace(snap, sig_bbo_trusted=False)
    assert h.coordinator._candidate_refresh_score(
        bad_bbo, signal, action=OrderAction.BUY,
    ) is None


def test_untrusted_depth_recheck_requests_only_profitable_candidates():
    h = _Harness(economics_gate_enabled=True)
    submitted = []
    h.coordinator._request_fresh_depth = lambda eid, score: submitted.append((eid, score))
    snap = replace(_snapshot(pm_bid=0.75, pm_ask=0.76), sig_depth_trusted=False)
    h.run(snap)
    assert submitted and submitted[0][1] > 0
    assert not h.plans


def test_corroborated_stream_can_execute_after_short_depth_ttl():
    from test_residual_taker_live import NOW
    depth_ns = NOW - 4_000_000_000
    h = _Harness(economics_gate_enabled=True)
    good = replace(_snapshot(), sig_depth_observed_ns=depth_ns,
                   sig_stream_depth_corroborated=True)
    h.run(good)
    assert len(h.plans) == 1
    assert h.coordinator._skipped_signals["economics_stale_data"] == 0

    old = _Harness(economics_gate_enabled=True)
    old.run(replace(good, sig_stream_depth_corroborated=False))
    assert old.plans == []
    assert old.coordinator._skipped_signals["economics_stale_data"] == 1


def test_stream_extension_has_absolute_age_and_fresh_bbo_cutoffs():
    from test_residual_taker_live import NOW
    h = _Harness(economics_gate_enabled=True)
    h.coordinator._max_sig_stream_depth_age_ns = 3_000_000_000
    old_book = replace(_snapshot(), sig_stream_depth_corroborated=True,
                       sig_depth_observed_ns=NOW - 4_000_000_000)
    h.run(old_book)
    assert not h.plans
    assert h.coordinator._skipped_signals["economics_stale_data"] == 1

    h2 = _Harness(economics_gate_enabled=True)
    stale_bbo = replace(_snapshot(), sig_stream_depth_corroborated=True,
                        sig_depth_observed_ns=NOW - 2_000_000_000,
                        sig_bbo_observed_ns=NOW - 1_000_000_001)
    h2.run(stale_bbo)
    assert not h2.plans


def test_precheck_bbo_ttl_is_independent_of_depth_ttl():
    from test_residual_taker_live import NOW
    h = _Harness(economics_gate_enabled=True)
    h.coordinator._max_sig_bbo_age_ns = 15_000_000_000
    old_bbo = replace(_snapshot(ask=140, pm_bid=0.75, pm_ask=0.76),
                      sig_bbo_observed_ns=NOW - 4_000_000_000)
    score = h.coordinator._candidate_refresh_score(
        old_bbo, SimpleNamespace(quantity=50, direction="BUY"),
        action=OrderAction.BUY)
    assert score is not None and score > 0
