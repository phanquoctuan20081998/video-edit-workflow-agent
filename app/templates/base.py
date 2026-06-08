"""Base class for parametrized Manim scene templates."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ParametrizedScene(ABC):
    """A template that accepts a params dict and returns Manim CE source code."""

    name: str = ""
    description: str = ""
    params_schema: dict = {}   # JSON Schema for params validation

    @abstractmethod
    def render(self, params: dict[str, Any]) -> str:
        """Return complete Manim CE Python source string."""
        ...


_REGISTRY: dict[str, type[ParametrizedScene]] = {}


def register(cls: type[ParametrizedScene]) -> type[ParametrizedScene]:
    _REGISTRY[cls.name] = cls
    return cls


def render_template(template_name: str, params: dict[str, Any]) -> str:
    if template_name not in _REGISTRY:
        raise KeyError(f"Unknown template: {template_name!r}. Available: {list(_REGISTRY)}")
    return _REGISTRY[template_name]().render(params)


def list_templates() -> list[dict]:
    return [{"name": k, "description": v.description} for k, v in _REGISTRY.items()]
