"""Parametrized Manim scene templates.

LLM fills params dict → template emits valid Manim CE source string.
Prefer templates over free-form codegen when the visual type is covered here.
"""

from .base import ParametrizedScene, render_template, list_templates

# Import all template modules to trigger @register decorators
from . import charts, functions, geometry, matrix, signal, vectors  # noqa: F401

__all__ = ["ParametrizedScene", "render_template", "list_templates"]
