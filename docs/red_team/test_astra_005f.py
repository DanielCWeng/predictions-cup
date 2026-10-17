"""Source-extracted frozen function test plus live state invariants; no model refit."""
import ast
from pathlib import Path
from types import SimpleNamespace
from datetime import UTC, datetime
import numpy as np
import pytest
from predictions_cup.shadow.frozen_runtime import IncrementalHazard005FState, Hazard005FBboObservation
NS=1_000_000_000
ROOT=Path(__file__).resolve().parents[2]

@pytest.mark.xfail(strict=True,reason="ASTRA-007: floor-labeled capture bins leak up to 5s")
@pytest.mark.parametrize('lane',['train_dev','fit_freeze','holdout'])
def test_frozen_rolling_count_excludes_future_changes(lane):
    path=ROOT/'scripts'/'kaggle'/f'experiment_005f_{lane}'/'run.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='rolling_counts')
    namespace={'np':np,'NS':NS}
    exec(compile(ast.Module(body=[function],type_ignores=[]),str(path),'exec'),namespace)
    # Real event arrives at 19s. Frozen load_book_state labels its 5s bin 15s.
    actual=namespace['rolling_counts'](np.array([15*NS]),np.array([1]),np.array([15*NS]),15)
    assert actual[0] == 0

def state():
    obj=IncrementalHazard005FState(grid_origin_ns=0,scope_resolver=lambda _: 'x')
    for t,bid,ask in [(0,.4,.6),(10,.42,.6),(20,.44,.6)]:
        obj.observe(Hazard005FBboObservation('x',t*NS,t*NS,bid,ask,'fixture'))
    return obj

@pytest.mark.xfail(strict=True,reason="ASTRA-008: query ignores latest invalidation")
def test_runtime_state_invalidated_book_not_usable():
    obj=state()
    obj.observe(Hazard005FBboObservation('x',25*NS,25*NS,None,None,'fixture',ambiguous=True))
    vector=obj.feature_vector(SimpleNamespace(observed_at=datetime.fromtimestamp(30,tz=UTC)))
    assert vector is None

@pytest.mark.xfail(strict=True,reason="ASTRA-008: last valid state remains usable indefinitely")
def test_runtime_state_no_observations_for_hours_not_usable():
    vector=state().feature_vector(SimpleNamespace(observed_at=datetime.fromtimestamp(3600,tz=UTC)))
    assert vector is None
