"""Parameter-surface diagnostics favouring stable plateaus over isolated optima."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ParameterCell:
    coordinates: tuple[int, ...]
    parameters: tuple[tuple[str, str], ...]
    metric: Decimal


@dataclass(frozen=True, slots=True)
class StabilityCell:
    cell: ParameterCell
    rank: int
    neighbouring_cells: int
    sign_stability: Decimal
    distance_from_peak: Decimal
    local_dispersion: Decimal
    neighbour_fraction_within_tolerance: Decimal


@dataclass(frozen=True, slots=True)
class ParameterSurfaceReport:
    tolerance: Decimal
    cells: tuple[StabilityCell, ...]
    peak_coordinates: tuple[int, ...]
    peak_is_isolated: bool


def parameter_surface(
    cells: tuple[ParameterCell, ...], *, tolerance: Decimal
) -> ParameterSurfaceReport:
    if not cells:
        raise ValueError("parameter surface requires cells")
    if tolerance < 0:
        raise ValueError("tolerance must be non-negative")
    by_coord = {cell.coordinates: cell for cell in cells}
    if len(by_coord) != len(cells):
        raise ValueError("parameter coordinates must be unique")
    ranked = sorted(cells, key=lambda item: (-item.metric, item.coordinates))
    rank = {cell.coordinates: index for index, cell in enumerate(ranked, 1)}
    peak = ranked[0]
    output: list[StabilityCell] = []
    for cell in sorted(cells, key=lambda item: item.coordinates):
        neighbours = [
            candidate
            for candidate in cells
            if _manhattan(cell.coordinates, candidate.coordinates) == 1
        ]
        if neighbours:
            same_sign = sum(
                1
                for candidate in neighbours
                if (candidate.metric > 0) == (cell.metric > 0)
                or (candidate.metric == 0 and cell.metric == 0)
            )
            sign_stability = Decimal(same_sign) / Decimal(len(neighbours))
            mean = sum(
                (candidate.metric for candidate in neighbours), Decimal("0")
            ) / Decimal(len(neighbours))
            dispersion = sum(
                (abs(candidate.metric - mean) for candidate in neighbours),
                Decimal("0"),
            ) / Decimal(len(neighbours))
            scale = max(abs(peak.metric), Decimal("0.000000000001"))
            within = sum(
                1
                for candidate in neighbours
                if abs(peak.metric - candidate.metric) / scale <= tolerance
            )
            neighbour_fraction = Decimal(within) / Decimal(len(neighbours))
        else:
            sign_stability = Decimal("0")
            dispersion = Decimal("0")
            neighbour_fraction = Decimal("0")
        output.append(
            StabilityCell(
                cell=cell,
                rank=rank[cell.coordinates],
                neighbouring_cells=len(neighbours),
                sign_stability=sign_stability,
                distance_from_peak=peak.metric - cell.metric,
                local_dispersion=dispersion,
                neighbour_fraction_within_tolerance=neighbour_fraction,
            )
        )
    peak_row = next(item for item in output if item.cell.coordinates == peak.coordinates)
    return ParameterSurfaceReport(
        tolerance=tolerance,
        cells=tuple(output),
        peak_coordinates=peak.coordinates,
        peak_is_isolated=peak_row.neighbour_fraction_within_tolerance == 0,
    )


def _manhattan(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    if len(left) != len(right):
        return 999999
    return sum(abs(a - b) for a, b in zip(left, right, strict=True))
