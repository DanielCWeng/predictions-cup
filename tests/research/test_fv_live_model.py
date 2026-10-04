from __future__ import annotations

import pytest

from predictions_cup.research.fv_live.model import (
    INVENTORY_SKEW_GRID_TICKS_PER_500,
    FVParameters,
    fair_value,
    frozen_ofi_probability,
    quote_center,
)


def test_ofi_frozen_probability_is_centered_and_bounded() -> None:
    p = frozen_ofi_probability(
        (
            0.49926132703457154,
            0.6093899214720409,
            1255.7557112181355,
            46.28957943495199,
            0.00651951604757511,
            0.0050633144068354,
            0.00007383662075523766,
            0.00015296481487162234,
            0.0003953494884767887,
        )
    )
    assert p == pytest.approx(1 / (1 + __import__("math").exp(-0.003167796092707855)))
    assert 0.0 < p < 1.0


def test_fair_value_layers_ablate_independently() -> None:
    params = FVParameters(reversal_beta=-0.2, ofi_price_beta=0.1, hazard_pull_threshold=0.8)
    assert fair_value(0.5, 0.02, 0.7, params, use_ofi=False) == pytest.approx(0.496)
    assert fair_value(0.5, 0.02, 0.7, params, use_reversal=False) == pytest.approx(0.52)
    assert fair_value(0.5, 0.02, 0.7, params) == pytest.approx(0.516)


@pytest.mark.parametrize("k", INVENTORY_SKEW_GRID_TICKS_PER_500)
def test_inventory_skew_grid_and_sign(k: float) -> None:
    assert quote_center(0.5, 500, k) == pytest.approx(0.5 - k * 0.01)


def test_inventory_skew_rejects_undeclared_value() -> None:
    with pytest.raises(ValueError, match="predeclared"):
        quote_center(0.5, 100, 0.75)
