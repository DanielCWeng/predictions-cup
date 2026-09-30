from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

from predictions_cup.analysis.live_005f import (
    StateTransferSample,
    genuine_age_bucket,
    summarize_state_transfer,
)
from predictions_cup.maker.contracts import (
    ExternalQuoteState,
    MakerMarketSnapshot,
)
from predictions_cup.mapping.crosswalk import load_document
from predictions_cup.mapping.models import MappingDirection, MappingStatus
from predictions_cup.runtime.models import (
    RuntimeBook,
    RuntimeLevel,
    RuntimeMarket,
    RuntimePortfolio,
    RuntimeSnapshot,
)
from predictions_cup.shadow import (
    CanonicalShadowSnapshot,
    FixedHazard005FRegimeProvider,
    Frozen005FEvaluator,
)
from predictions_cup.shadow.live_005f import Live005FStateProvider

NS = 1_000_000_000
BASE_MONO = 20_000_000_000_000
BASE = datetime(2026, 10, 1, 16, 0, tzinfo=UTC)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAPPING_PATH = PROJECT_ROOT / "data/mappings/sig_polymarket_2026.json"


def _identity() -> tuple[str, str, str]:
    mapping = load_document(MAPPING_PATH)
    for record in mapping.records:
        if (
            record.status is MappingStatus.VERIFIED
            and record.direct_polymarket is not None
            and record.mapping_direction
            in {MappingDirection.SAME, MappingDirection.COMPLEMENT}
        ):
            return (
                record.sig_market_id,
                record.sig_exchange_id,
                record.direct_polymarket.mapped_token_id,
            )
    raise AssertionError("fixture mapping has no verified direct PM relation")


def _snapshot(
    seconds: float,
    bid: float,
    ask: float,
    *,
    quote_observed_at: datetime | None = None,
) -> CanonicalShadowSnapshot:
    market_id, exchange_id, token_id = _identity()
    now = BASE_MONO + int(seconds * NS)
    runtime = RuntimeSnapshot(
        markets=(
            RuntimeMarket(
                market_id=market_id,
                status="open",
                exchange_ids=(exchange_id,),
                tournament_id="SIG-2026",
                mapping_accepted=True,
                tradeable=True,
            ),
        ),
        books=(
            RuntimeBook(
                exchange_id=exchange_id,
                market_id=market_id,
                tournament_id="SIG-2026",
                bids=(RuntimeLevel(price_ticks=90, quantity=10.0),),
                asks=(RuntimeLevel(price_ticks=110, quantity=10.0),),
                trusted_depth=True,
                observed_monotonic_ns=now,
            ),
        ),
        portfolio=RuntimePortfolio(account_trusted=True),
        observation_monotonic_ns=now,
    )
    observed_at = BASE + timedelta(seconds=seconds)
    maker = MakerMarketSnapshot(
        runtime=runtime,
        exchange_id=exchange_id,
        market_id=market_id,
        tournament_id="SIG-2026",
        now_monotonic_ns=now,
        sig_bbo_observed_ns=now,
        sig_bbo_trusted=True,
        sig_depth_observed_ns=now,
        sig_depth_trusted=True,
        account_observed_ns=now,
        inventory_observed_ns=now,
        external_quotes={
            token_id: ExternalQuoteState(
                token_id=token_id,
                best_bid=bid,
                best_ask=ask,
                observed_monotonic_ns=now,
                trusted=True,
                source_version="test-pm",
                observed_at=quote_observed_at or observed_at,
            )
        },
    )
    return CanonicalShadowSnapshot.freeze(
        maker,
        observed_at=observed_at,
        mapping_version="test-mapping",
        source_revision=f"test-{seconds}",
    )


def _provider(*, grid: bool = True) -> Live005FStateProvider:
    return Live005FStateProvider(
        mapping=load_document(MAPPING_PATH),
        grid_origin=BASE if grid else None,
    )


def _prime(provider: Live005FStateProvider) -> CanonicalShadowSnapshot:
    rows = (
        (0.0, 0.40, 0.60),
        (10.0, 0.40, 0.60),
        (20.0, 0.42, 0.60),
        (30.0, 0.42, 0.60),
        (45.0, 0.44, 0.60),
    )
    for seconds, bid, ask in rows:
        provider.feature_vector(_snapshot(seconds, bid, ask))
    query = _snapshot(60.0, 0.44, 0.60)
    return query


def test_005f_resolver_uses_accepted_pm_token_not_sig_market_id() -> None:
    market_id, exchange_id, token_id = _identity()
    provider = _provider()
    assert provider.scope_for_exchange(exchange_id) == token_id
    if market_id != token_id:
        assert provider.scope_for_exchange(exchange_id) != market_id


def test_live_provider_matches_exact_frozen_state_semantics() -> None:
    provider = _provider()
    query = _prime(provider)
    vector = provider.feature_vector(query)
    assert vector is not None
    assert vector.values["genuine_age_s"] == 15.0
    assert vector.values["genuine_15"] == 0.0
    assert vector.values["genuine_60"] == 2.0
    assert vector.values["abs_ret_15"] == 0.0
    assert math.isfinite(vector.values["rv_60"])
    assert vector.values["rv_60"] > 0.0


def test_live_provider_preserves_subsecond_genuine_change_time() -> None:
    provider = _provider()
    provider.feature_vector(_snapshot(0.0, 0.40, 0.60))
    provider.feature_vector(_snapshot(0.5, 0.41, 0.60))
    vector = provider.feature_vector(_snapshot(15.0, 0.41, 0.60))
    assert vector is not None
    assert vector.values["genuine_age_s"] == 14.5


def test_grid_origin_is_not_invented() -> None:
    provider = _provider(grid=False)
    query = _prime(provider)
    assert provider.grid_origin_ns is None
    assert provider.feature_vector(query) is None


def test_missing_external_event_time_fails_state_ingestion_transparently() -> None:
    provider = _provider()
    snapshot = _snapshot(
        1.0,
        0.40,
        0.60,
        quote_observed_at=None,
    )
    token_id = next(iter(snapshot.maker.external_quotes))
    quote = snapshot.maker.external_quotes[token_id]
    maker = MakerMarketSnapshot(
        runtime=snapshot.maker.runtime,
        exchange_id=snapshot.exchange_id,
        market_id=snapshot.market_id,
        tournament_id=snapshot.tournament_id,
        now_monotonic_ns=snapshot.maker.now_monotonic_ns,
        sig_bbo_observed_ns=snapshot.maker.sig_bbo_observed_ns,
        sig_bbo_trusted=snapshot.maker.sig_bbo_trusted,
        sig_depth_observed_ns=snapshot.maker.sig_depth_observed_ns,
        sig_depth_trusted=snapshot.maker.sig_depth_trusted,
        account_observed_ns=snapshot.maker.account_observed_ns,
        inventory_observed_ns=snapshot.maker.inventory_observed_ns,
        external_quotes={
            token_id: ExternalQuoteState(
                token_id=quote.token_id,
                best_bid=quote.best_bid,
                best_ask=quote.best_ask,
                observed_monotonic_ns=quote.observed_monotonic_ns,
                trusted=quote.trusted,
                source_version=quote.source_version,
                observed_at=None,
            )
        },
    )
    no_time = CanonicalShadowSnapshot.freeze(
        maker,
        observed_at=snapshot.observed_at,
        mapping_version=snapshot.mapping_version,
        source_revision="missing-event-time",
    )
    assert provider.feature_vector(no_time) is None
    assert provider.missing_event_time == 1


def test_serialized_model_absence_remains_fail_closed() -> None:
    provider = _provider()
    query = _prime(provider)
    evaluator = Frozen005FEvaluator(
        provider,
        FixedHazard005FRegimeProvider("PRE_ELECTION"),
    )
    metadata = evaluator.metadata(query)
    assert not metadata.ready
    assert metadata.readiness_reason is not None
    assert metadata.readiness_reason.startswith("model_artifact_missing")


def test_state_transfer_buckets_are_predeclared_and_non_directional() -> None:
    assert genuine_age_bucket(0.0) == "AGE_0_5"
    assert genuine_age_bucket(5.0) == "AGE_5_15"
    assert genuine_age_bucket(15.0) == "AGE_15_60"
    assert genuine_age_bucket(60.0) == "AGE_60_PLUS"

    samples = tuple(
        StateTransferSample(
            decision_id=f"d{index}",
            market_id="m1",
            exchange_id="e1",
            horizon_seconds=5,
            state_bucket="AGE_0_5",
            regime="PRE_ELECTION",
            genuine_age_s=2.0,
            genuine_15=2.0,
            genuine_60=4.0,
            abs_ret_15=0.01,
            rv_60=0.02,
            state_version="v1",
            fill_rate=1.0,
            markout=-0.01,
            adverse_selection=0.01,
            spread_capture=0.005,
            realised_pnl=None,
            net_economic_metric=-0.01,
            net_economic_metric_name="diagnostic_post_fill_markout",
            evidence_refs=("build009:fill",),
        )
        for index in range(5)
    )
    row = summarize_state_transfer(samples)[0]
    assert row["status"] == "DESCRIPTIVE_ONLY"
    assert row["realised_pnl"] is None
    assert "direction" not in row
    assert "BUY" not in str(row)
    assert "SELL" not in str(row)
