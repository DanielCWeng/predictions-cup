"""Research-only snapback row comparison with explicit episode-method admission.

This checks metadata consistency, not issuer provenance or real P&L. The caller's
verified_source_rules mapping must be independently reviewed: no self-declared
commit, version, config hash or paper/source trust flag is sufficient.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

ANALYSIS_VERSION = "live-diag-001-v2"
EPISODE_RULE_VERSION = "signed-threshold-episodes-v2"
SNAPBACK_METHOD_VERSION = "snapback-signed-threshold-episodes-v2"
TRIGGER_RULE = "signed_threshold_crossing_rearms_on_direct_sign_reversal_or_below_threshold"
INCOMPARABLE = "INCOMPARABLE_EPISODE_METHOD"

# An isolated Parquet row must have enough information to reject cross-run
# comparisons without relying on a sidecar or missing append-only history.
_CONTEXT = (
    "market_id",
    "exchange_id",
    "polymarket_token_id",
    "mapping_class",
    "mapping_direction",
    "mapping_hash",
    "threshold_ticks",
    "tick_size",
    "max_quote_age_seconds",
    "config_hash",
)


def compare_snapback_rows(
    earlier: Mapping[str, object],
    later: Mapping[str, object],
    *,
    metric_name: str,
    verified_source_rules: Mapping[str, str],
) -> tuple[str, float | None]:
    """Return descriptive metric delta or a hard NULL for ambiguous cohorts.

    Legacy v1 (including experimental signed-X2 output mislabeled v1), unknown
    commit provenance, mismatched rules, horizon, mapping and config fail closed.
    This never makes an assertion about realised profit or source admission.
    """
    for row in (earlier, later):
        if (
            row.get("analysis_version") != ANALYSIS_VERSION
            or row.get("method_version") != SNAPBACK_METHOD_VERSION
            or row.get("episode_rule_version") != EPISODE_RULE_VERSION
            or row.get("trigger_rule") != TRIGGER_RULE
        ):
            return INCOMPARABLE, None
        git_sha = row.get("git_sha")
        if (
            not isinstance(git_sha, str)
            or len(git_sha) != 40
            or any(character not in "0123456789abcdef" for character in git_sha)
            or verified_source_rules.get(git_sha) != EPISODE_RULE_VERSION
        ):
            return INCOMPARABLE, None

    if any(
        earlier.get(key) is None
        or later.get(key) is None
        or earlier[key] != later[key]
        for key in _CONTEXT
    ):
        return INCOMPARABLE, None

    if not metric_name.startswith("fraction_gap_closed_") or not metric_name.endswith("s"):
        return INCOMPARABLE, None
    horizon = metric_name.removeprefix("fraction_gap_closed_").removesuffix("s")
    if not horizon.isdecimal() or int(horizon) <= 0:
        return INCOMPARABLE, None

    old_value, new_value = earlier.get(metric_name), later.get(metric_name)
    if (
        isinstance(old_value, bool)
        or isinstance(new_value, bool)
        or not isinstance(old_value, (int, float))
        or not isinstance(new_value, (int, float))
        or not math.isfinite(old_value)
        or not math.isfinite(new_value)
    ):
        return INCOMPARABLE, None
    return "DESCRIPTIVE_COMPARISON_ONLY", float(new_value - old_value)
