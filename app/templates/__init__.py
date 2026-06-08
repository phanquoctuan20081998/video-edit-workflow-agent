"""Parametrized Manim scene templates.

LLM fills params dict → template emits valid Manim CE source string.
Prefer templates over free-form codegen when the visual type is covered here.
"""

from .base import ParametrizedScene, render_template

__all__ = ["ParametrizedScene", "render_template"]
