"""MM-REPLAY-001 research primitives.

This module is deliberately data-source agnostic. It provides the provenance gate,
schema audit, canonical order-book events, fill-model boundary, fair-value quote
construction, markouts, latency helpers, and a thin adapter around the *existing*
frozen EXPERIMENT-005F feature implementation.

It never places orders and never silently substitutes a dataset.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal, Protocol, cast

import pyarrow.parquet as pq

from predictions_cup.runtime.models import SIG_TICK
from predictions_cup.shadow.contracts import CanonicalShadowSnapshot
from predictions_cup.shadow.frozen_runtime import (
    Hazard005FBboObservation,
    IncrementalHazard005FState,
)

EXPERIMENT_ID: Final = "MM-REPLAY-001"
DATA_STATUS_WAITING: Final = "WAITING_FOR_DATA"
DATA_STATUS_BOUND: Final = "BOUND"
SCIENTIFIC_RESULT_NOT_RUN: Final = "NOT_RUN"
TICK: Final = float(SIG_TICK)

MARKOUT_HORIZONS_S: Final[tuple[int, ...]] = (1, 5, 15, 30, 60, 300)