"""DATA-001 EXPERIMENT-002 smoke gate: real corpus rows through unmodified replay machinery.

The question answered is purely mechanical: can accepted BUILD-005 loading and the
EXPERIMENT-002 runner consume normalized historical observations without manual rewriting?
Parameters are fixed smoke settings, not tuned, and results are not evidence of edge.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from predictions_cup.learning.relationships import (
    ExperimentFamily,
    InstrumentKey,
    ReferenceSpec,
    RelationshipExperimentRunner,
    RelationshipExperimentSpec,
    serialize_relationship_observations,
)
from predictions_cup.replay import CaptureSelection, ReplaySource, load_polymarket_capture

SMOKE_THRESHOLD = Decimal("0.01")
SMOKE_LOOKBACK = timedelta(seconds=30)
SMOKE_FRESHNESS = timedelta(minutes=5)


def run_smoke(
    books_dir: Path,
    *,
    dataset_id: str,
    target_token: str,
    reference_token: str,
    start_at: datetime,
    end_at: datetime,
) -> dict[str, Any]:
    selection = CaptureSelection(
        start_at=start_at,
        end_at=end_at,
        polymarket_token_ids=(target_token, reference_token),
    )
    events = load_polymarket_capture(books_dir, selection=selection)
    spec = RelationshipExperimentSpec(
        id="DATA-001-SMOKE-LEADLAG",
        dataset_id=dataset_id,
        family=ExperimentFamily.LEADLAG,
        target=InstrumentKey(ReplaySource.POLYMARKET, target_token),
        references=(ReferenceSpec(InstrumentKey(ReplaySource.POLYMARKET, reference_token)),),
        threshold=SMOKE_THRESHOLD,
        lookback=SMOKE_LOOKBACK,
        reference_freshness=SMOKE_FRESHNESS,
        target_freshness=SMOKE_FRESHNESS,
    )
    observations = RelationshipExperimentRunner().run(events, (spec,))
    serialized = serialize_relationship_observations(observations)
    reasons = Counter(
        o.invalid_reason.value for o in observations if o.invalid_reason is not None
    )
    return {
        "question": "Can BUILD-005 + EXPERIMENT-002 consume real DATA-001 rows unmodified?",
        "dataset_id": dataset_id,
        "books_dir_layout": "<corpus>/schema_version=1/<regime>/books",
        "selection": {
            "start_at": start_at.isoformat(),
            "end_at_exclusive": end_at.isoformat(),
            "target_token": target_token,
            "reference_token": reference_token,
        },
        "spec": {
            "family": spec.family.value,
            "threshold": str(SMOKE_THRESHOLD),
            "lookback_seconds": SMOKE_LOOKBACK.total_seconds(),
            "freshness_seconds": SMOKE_FRESHNESS.total_seconds(),
            "horizons_seconds": [h.total_seconds() for h in spec.target_horizons],
        },
        "replay_events_loaded": len(events),
        "replay_events_by_type": dict(sorted(Counter(e.event_type.value for e in events).items())),
        "observations": len(observations),
        "valid_observations": sum(1 for o in observations if o.valid),
        "invalid_reasons": dict(sorted(reasons.items())),
        "serialized_sha256": hashlib.sha256(serialized).hexdigest(),
        "interpretation": "mechanical compatibility only; not evidence of trading edge",
    }
