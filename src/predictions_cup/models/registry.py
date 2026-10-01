"""Explicit startup registry for MODEL-RUNTIME-001."""

from __future__ import annotations

from collections.abc import Iterable

from predictions_cup.models.contracts import ModelProvider


class ModelRegistry:
    """Small explicit registry; no filesystem scanning or hot-path imports."""

    def __init__(self, providers: Iterable[ModelProvider] = ()) -> None:
        self._providers: dict[str, ModelProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: ModelProvider) -> None:
        model_id = provider.spec.model_id
        if model_id in self._providers:
            current = self._providers[model_id]
            raise ValueError(
                "duplicate model identity/version collision: "
                f"{model_id}@{current.spec.model_version}"
            )
        self._providers[model_id] = provider

    def unregister(self, model_id: str) -> ModelProvider:
        try:
            return self._providers.pop(model_id)
        except KeyError as exc:
            raise KeyError(f"unknown model: {model_id}") from exc

    def get(self, model_id: str) -> ModelProvider:
        try:
            return self._providers[model_id]
        except KeyError as exc:
            raise KeyError(f"unknown model: {model_id}") from exc

    def ids(self) -> tuple[str, ...]:
        return tuple(self._providers)

    def providers(self) -> tuple[ModelProvider, ...]:
        return tuple(self._providers.values())

    def resolve(self, model_ids: Iterable[str]) -> tuple[ModelProvider, ...]:
        return tuple(self.get(model_id) for model_id in model_ids)


def parse_model_ids(raw: str) -> tuple[str, ...]:
    """Parse one startup allowlist once; duplicate/blank entries fail closed."""

    if not raw.strip():
        return ()
    values = tuple(part.strip() for part in raw.split(","))
    if any(not value for value in values):
        raise ValueError("model allowlist contains a blank model id")
    if len(values) != len(set(values)):
        raise ValueError("model allowlist contains duplicate model ids")
    return values


def default_model_registry() -> ModelRegistry:
    """Explicit import/registration point.  Add one entry for each accepted model."""

    from predictions_cup.models.model_fixture_context import FixtureContextModel
    from predictions_cup.models.model_fixture_direction import FixtureDirectionModel
    from predictions_cup.models.model_no_trade import NoTradeModel

    return ModelRegistry(
        (
            NoTradeModel(),
            FixtureDirectionModel(),
            FixtureContextModel(),
        )
    )
