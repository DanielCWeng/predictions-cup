"""Leakage-safe participant×role features for EXPERIMENT-005A/005C."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ParticipantRoleScore:
    participant: str
    role: str
    count: int
    raw_mean: float
    shrunk_mean: float


def shrink_mean(total: float, count: int, *, prior_mean: float, prior_count: float) -> float:
    if count < 0 or prior_count < 0:
        raise ValueError("counts must be non-negative")
    denominator = float(count) + float(prior_count)
    if denominator <= 0:
        return float(prior_mean)
    return (float(total) + float(prior_count) * float(prior_mean)) / denominator


def fit_participant_role_scores(
    participants: np.ndarray,
    roles: np.ndarray,
    outcomes: np.ndarray,
    *,
    minimum_history: int,
    prior_count: float,
    prior_mean: float = 0.0,
    excluded_participants: frozenset[str] = frozenset(),
) -> dict[tuple[str, str], ParticipantRoleScore]:
    """Fit role-specific scores from an already time-restricted training sample."""

    if not (len(participants) == len(roles) == len(outcomes)):
        raise ValueError("participant, role and outcome lengths differ")
    if minimum_history < 1:
        raise ValueError("minimum_history must be positive")

    totals: dict[tuple[str, str], float] = defaultdict(float)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for participant, role, outcome in zip(participants, roles, outcomes, strict=True):
        if not np.isfinite(float(outcome)):
            continue
        identity = str(participant).lower()
        role_name = str(role).upper()
        if identity in excluded_participants:
            continue
        if role_name not in {"TAKER", "MAKER"}:
            continue
        key = (identity, role_name)
        totals[key] += float(outcome)
        counts[key] += 1

    result: dict[tuple[str, str], ParticipantRoleScore] = {}
    for key in sorted(counts):
        count = counts[key]
        if count < minimum_history:
            continue
        raw_mean = totals[key] / count
        result[key] = ParticipantRoleScore(
            participant=key[0],
            role=key[1],
            count=count,
            raw_mean=float(raw_mean),
            shrunk_mean=shrink_mean(
                totals[key],
                count,
                prior_mean=prior_mean,
                prior_count=prior_count,
            ),
        )
    return result


def score_participants(
    participants: np.ndarray,
    roles: np.ndarray,
    scores: dict[tuple[str, str], ParticipantRoleScore],
    *,
    neutral_fallback: float = 0.0,
) -> np.ndarray:
    if len(participants) != len(roles):
        raise ValueError("participant and role lengths differ")
    result = np.full(len(participants), float(neutral_fallback), dtype=np.float64)
    for index, (participant, role) in enumerate(zip(participants, roles, strict=True)):
        score = scores.get((str(participant).lower(), str(role).upper()))
        if score is not None:
            result[index] = score.shrunk_mean
    return result


def expanding_cross_fitted_scores(
    times_ns: np.ndarray,
    participants: np.ndarray,
    roles: np.ndarray,
    outcomes: np.ndarray,
    *,
    fold_boundaries_ns: np.ndarray,
    embargo_ns: int,
    minimum_history: int,
    prior_count: float,
    neutral_fallback: float = 0.0,
    excluded_participants: frozenset[str] = frozenset(),
) -> tuple[np.ndarray, np.ndarray]:
    """Encode validation intervals using history strictly before each boundary.

    fold_boundaries_ns are ordered validation starts. For each boundary b_i, rows in
    [b_i, b_{i+1}) (or [b_i, +inf) for the last boundary) are scored from rows with
    time < b_i - embargo. No validation outcome can enter its own or an earlier encoding.
    """

    times = np.asarray(times_ns, np.int64)
    values = np.asarray(outcomes, float)
    boundaries = np.asarray(fold_boundaries_ns, np.int64)
    if not (len(times) == len(participants) == len(roles) == len(values)):
        raise ValueError("cross-fit input lengths differ")
    if np.any(np.diff(boundaries) <= 0):
        raise ValueError("fold boundaries must be strictly increasing")
    if embargo_ns < 0:
        raise ValueError("embargo must be non-negative")

    encoded = np.full(len(times), float(neutral_fallback), dtype=np.float64)
    fold_id = np.full(len(times), -1, dtype=np.int64)
    for number, start in enumerate(boundaries):
        end = boundaries[number + 1] if number + 1 < len(boundaries) else np.iinfo(np.int64).max
        validation = (times >= start) & (times < end)
        training = times < int(start - embargo_ns)
        fitted = fit_participant_role_scores(
            participants[training],
            roles[training],
            values[training],
            minimum_history=minimum_history,
            prior_count=prior_count,
            excluded_participants=excluded_participants,
        )
        encoded[validation] = score_participants(
            participants[validation],
            roles[validation],
            fitted,
            neutral_fallback=neutral_fallback,
        )
        fold_id[validation] = number
    return encoded, fold_id


def permute_participant_identity_within_strata(
    participants: np.ndarray,
    strata: np.ndarray,
    *,
    seed: int,
) -> np.ndarray:
    """Permutation null preserving identity multiset inside each stratum."""

    if len(participants) != len(strata):
        raise ValueError("participant and strata lengths differ")
    rng = np.random.default_rng(seed)
    result = np.asarray(participants, dtype=object).copy()
    stratum_values = np.asarray(strata, dtype=object)
    string_strata = np.asarray([str(item) for item in stratum_values], dtype=object)
    for value in sorted(set(map(str, stratum_values))):
        indices = np.flatnonzero(string_strata == value)
        shuffled = result[indices].copy()
        rng.shuffle(shuffled)
        result[indices] = shuffled
    return result


def deterministic_participant_seed(master: int, component: str) -> int:
    digest = hashlib.sha256(f"{master}|participant|{component}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def expanding_available_participant_role_scores(
    times_ns: np.ndarray,
    label_available_ns: np.ndarray,
    participants: np.ndarray,
    roles: np.ndarray,
    outcomes: np.ndarray,
    *,
    embargo_ns: int,
    minimum_history: int,
    prior_count: float,
    prior_mean: float = 0.0,
    neutral_fallback: float = 0.0,
    excluded_participants: frozenset[str] = frozenset(),
) -> tuple[np.ndarray, np.ndarray]:
    """Past-only participant-role scores using labels only after they become observable.

    A historical row enters the score state only when its label-available time is strictly
    earlier than current time minus embargo. Rows sharing one current timestamp are all
    scored before any newly eligible label at that timestamp can be used.
    """

    times = np.asarray(times_ns, np.int64)
    available = np.asarray(label_available_ns, np.int64)
    participant_values = np.asarray(participants, object)
    role_values = np.asarray(roles, object)
    target = np.asarray(outcomes, float)
    n = len(times)
    if not (
        len(available)
        == len(participant_values)
        == len(role_values)
        == len(target)
        == n
    ):
        raise ValueError("available-score input lengths differ")
    if embargo_ns < 0 or minimum_history < 1 or prior_count < 0:
        raise ValueError("invalid embargo/history/prior setting")

    encoded = np.full(n, float(neutral_fallback), np.float64)
    history_count = np.zeros(n, np.int64)
    eval_order = np.argsort(times, kind="stable")
    label_order = np.argsort(available, kind="stable")

    totals: dict[tuple[str, str], float] = defaultdict(float)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    cursor = 0

    batch_start = 0
    while batch_start < n:
        batch_end = batch_start + 1
        current_time = int(times[eval_order[batch_start]])
        while batch_end < n and int(times[eval_order[batch_end]]) == current_time:
            batch_end += 1

        cutoff = current_time - embargo_ns
        while cursor < n and int(available[label_order[cursor]]) < cutoff:
            index = int(label_order[cursor])
            outcome = target[index]
            identity = str(participant_values[index]).lower()
            role = str(role_values[index]).upper()
            if (
                np.isfinite(outcome)
                and identity not in excluded_participants
                and role in {"TAKER", "MAKER"}
            ):
                key = (identity, role)
                totals[key] += float(outcome)
                counts[key] += 1
            cursor += 1

        for index_value in eval_order[batch_start:batch_end]:
            index = int(index_value)
            identity = str(participant_values[index]).lower()
            role = str(role_values[index]).upper()
            if identity in excluded_participants or role not in {"TAKER", "MAKER"}:
                continue
            key = (identity, role)
            count = counts.get(key, 0)
            history_count[index] = count
            if count < minimum_history:
                continue
            encoded[index] = shrink_mean(
                totals.get(key, 0.0),
                count,
                prior_mean=prior_mean,
                prior_count=prior_count,
            )
        batch_start = batch_end

    return encoded, history_count
