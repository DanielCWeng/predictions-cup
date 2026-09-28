"""Frozen model semantics shared by EXPERIMENT-005F fit-freeze and holdout.

This module contains no candidate selection logic and never reads empirical outcomes.
It translates already-frozen coordinate metadata into exact feature columns and
estimator classes.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping


def is_classification_target(dataset: str, target: str) -> bool:
    return dataset == "clock" and (
        target.startswith("update_h") or target.startswith("jump_h")
    )


def baseline_columns(dataset: str, target: str) -> tuple[str, ...]:
    """Return the exact frozen tournament baseline columns."""
    if dataset == "clock" and target.startswith("update_h"):
        return ("genuine_15", "genuine_60")
    if dataset == "clock" and target.startswith("jump_h"):
        return ("abs_ret_15", "rv_60")
    if dataset == "clock":
        return ("ret_15", "ret_30", "ret_60")
    if dataset == "event":
        return ("event_ret1", "event_ret2")
    if dataset == "depth":
        return ("ret_15", "ret_30", "ret_60")
    raise ValueError(f"unsupported dataset: {dataset!r}")


def candidate_columns(
    *,
    dataset: str,
    candidate: str,
    available_columns: Collection[str],
    microfv_blocks: Mapping[str, Collection[str]],
    base_columns: Collection[str],
) -> tuple[str, ...]:
    """Expand a selected candidate exactly as the frozen tournament does."""
    available = set(available_columns)
    base = set(base_columns)
    if dataset == "depth" and candidate in microfv_blocks:
        return tuple(
            col
            for col in microfv_blocks[candidate]
            if col in available and col not in base
        )
    if candidate in available and candidate not in base:
        return (candidate,)
    return ()


def full_feature_columns(
    *,
    dataset: str,
    target: str,
    candidate: str,
    available_columns: Collection[str],
    microfv_blocks: Mapping[str, Collection[str]],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    base = tuple(
        col for col in baseline_columns(dataset, target) if col in available_columns
    )
    challenger = candidate_columns(
        dataset=dataset,
        candidate=candidate,
        available_columns=available_columns,
        microfv_blocks=microfv_blocks,
        base_columns=base,
    )
    if not challenger:
        raise ValueError(
            f"selected candidate has no observable challenger columns: "
            f"{dataset=} {target=} {candidate=}"
        )
    return base, base + challenger


def build_frozen_model(
    model_name: str,
    *,
    classification: bool,
    random_state: int,
):
    """Instantiate one exact estimator from the frozen 005F model ladder.

    Scikit-learn is imported lazily because the repository's lightweight
    unit-test environment does not require the Kaggle research stack.
    """
    from sklearn.ensemble import (
        HistGradientBoostingClassifier,
        HistGradientBoostingRegressor,
    )
    from sklearn.linear_model import (
        ElasticNet,
        LinearRegression,
        LogisticRegression,
        Ridge,
    )
    if classification:
        if model_name.startswith("LOGIT_C"):
            c = float(model_name.removeprefix("LOGIT_C"))
            return LogisticRegression(
                C=c,
                max_iter=500,
                random_state=random_state,
            )
        if model_name.startswith("HGB_D"):
            depth, lr = _parse_hgb(model_name)
            return HistGradientBoostingClassifier(
                max_depth=depth,
                learning_rate=lr,
                max_iter=200,
                random_state=random_state,
            )
        raise ValueError(f"unsupported classification model: {model_name!r}")

    if model_name == "OLS":
        return LinearRegression()
    if model_name.startswith("RIDGE_"):
        alpha = float(model_name.removeprefix("RIDGE_"))
        return Ridge(alpha=alpha)
    if model_name.startswith("ENET_"):
        tail = model_name.removeprefix("ENET_")
        alpha_s, l1_s = tail.split("_", 1)
        return ElasticNet(
            alpha=float(alpha_s),
            l1_ratio=float(l1_s),
            max_iter=2000,
        )
    if model_name.startswith("HGB_D"):
        depth, lr = _parse_hgb(model_name)
        return HistGradientBoostingRegressor(
            max_depth=depth,
            learning_rate=lr,
            max_iter=200,
            random_state=random_state,
        )
    raise ValueError(f"unsupported regression model: {model_name!r}")


def _parse_hgb(model_name: str) -> tuple[int, float]:
    # Frozen names are HGB_D{2|3}_LR{0.03|0.1}.
    try:
        depth_s, lr_s = model_name.removeprefix("HGB_D").split("_LR", 1)
        depth = int(depth_s)
        lr = float(lr_s)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid HGB model name: {model_name!r}") from exc
    if depth not in {2, 3} or lr not in {0.03, 0.1}:
        raise ValueError(f"HGB model outside frozen grid: {model_name!r}")
    return depth, lr
