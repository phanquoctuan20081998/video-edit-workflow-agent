"""3Blue1Brown-style design system for Manim CE.

All generated scenes import STYLE_HEADER which sets background, palette,
and helper constants. Templates use these instead of raw color strings.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

# ── Canonical 3b1b palette ─────────────────────────────────────────────────────
# Background must be BACKGROUND_COLOR, never pure black or white.
BACKGROUND_COLOR = "#1C1C2E"   # deep navy, 3b1b default

PALETTE = {
    # Blues — primary mathematical objects
    "BLUE_E":   "#1C758A",   # deep teal (axes, grids)
    "BLUE_D":   "#29ABCA",   # mid blue
    "BLUE_C":   "#58C4DD",   # standard BLUE — vectors, primary objects
    "BLUE_B":   "#9CDCEB",   # light blue
    # Greens — secondary / supporting objects
    "GREEN_E":  "#699C52",
    "GREEN_C":  "#83C167",   # standard GREEN
    "TEAL_E":   "#49A88F",   # teal
    # Yellows / Gold — emphasis, answers, highlighted results
    "YELLOW":   "#FFFF00",   # pure yellow — use sparingly, max contrast
    "GOLD_E":   "#C49A04",   # warm gold — softer emphasis
    # Reds — negation, errors, important warnings
    "RED_C":    "#FC6255",   # standard RED
    "MAROON_B": "#C55F73",
    # Neutrals
    "WHITE":    "#FFFFFF",   # text, formulas, labels
    "GREY_B":   "#BDBDBD",   # secondary text
    "GREY_D":   "#55534E",   # grid lines, minor elements
}

# ── Style header injected at top of every generated scene ─────────────────────
# All generated code must include this block.
STYLE_HEADER = f'''\
from manim import *
import numpy as np

# 3b1b design system
BACKGROUND_COLOR = "{BACKGROUND_COLOR}"
P_BLUE   = "{PALETTE["BLUE_C"]}"
P_GREEN  = "{PALETTE["GREEN_C"]}"
P_YELLOW = "{PALETTE["YELLOW"]}"
P_GOLD   = "{PALETTE["GOLD_E"]}"
P_RED    = "{PALETTE["RED_C"]}"
P_TEAL   = "{PALETTE["TEAL_E"]}"
P_WHITE  = "{PALETTE["WHITE"]}"
P_GREY   = "{PALETTE["GREY_B"]}"
P_AXIS   = "{PALETTE["BLUE_E"]}"   # axes and grids
P_DIM    = "{PALETTE["GREY_D"]}"   # dashed guides, minor elements
'''

# ── Layout constants (in Manim world units) ────────────────────────────────────
# Frame: 14.22 wide × 8.0 tall (1920×1080 at default pixel_height)
FRAME_W = 14.22
FRAME_H = 8.0
MAX_FORMULA_WIDTH = 10.0   # scale MathTex down if wider than this
TITLE_BUFF = 0.5           # gap between title and frame edge
OBJECT_BUFF = 0.75         # standard gap between objects (MED_LARGE_BUFF)
LABEL_BUFF = 0.25          # gap between object and its label (SMALL_BUFF)

# ── Design rules (encoded as strings for the LLM prompt) ──────────────────────
DESIGN_RULES = """
## Visual design — 3Blue1Brown style

### Background
- config.background_color = BACKGROUND_COLOR  (deep navy #1C1C2E, set in construct())

### Color semantics — assign meaning, then stay consistent
- Primary objects (vectors, curves, key shapes): P_BLUE (#58C4DD)
- Secondary / supporting objects: P_GREEN (#83C167)
- Emphasis / final answer / highlighted result: P_YELLOW or P_GOLD
- Negation, cancellation, errors: P_RED (#FC6255)
- Axes, grids, number planes: P_AXIS (#1C758A)  — subdued, never dominant
- Dashed construction lines: P_DIM (#55534E)
- Text and formulas: P_WHITE (#FFFFFF)
- Secondary labels / annotations: P_GREY (#BDBDBD)

NEVER use random colors. Every color choice must communicate meaning.
If two objects share a color, the viewer infers they are the same concept.

### Typography
- MathTex for ALL mathematical expressions — never plain Text for formulas
- Scale MathTex down if width > 10 units: tex.scale(10 / tex.width)
- Font size for labels: 0.6–0.7 scale relative to scene text
- Title: Text or Tex, font_size=40, to_edge(UP, buff=0.5), color=P_WHITE

### Layout
- Never overlap objects. Use VGroup(...).arrange(DOWN, buff=0.75) for stacks
- Use to_edge(), next_to(), move_to() — never hardcode .shift(3.14159)
- Leave margin: nothing closer than 0.5 units to frame edge
- Max objects visible simultaneously: 6-8. More → split into sub-scenes

### Animation rhythm (the most important rule)
- Reveal INCREMENTALLY — one idea per play() call
- Always self.wait(1) after a major reveal. self.wait(0.5) between minor beats
- Use Write() for text/formulas (draws stroke by stroke — feels mathematical)
- Use Create() for shapes and lines
- Use FadeIn() only for background/context objects, never for the hero math
- Use Transform() / ReplacementTransform() to show one thing BECOMING another
  (this is the core 3b1b technique — equation A → equation B, shape → shape)
- Use Indicate() or Circumscribe() to focus attention without moving the object
- Use SurroundingRectangle(obj, color=P_YELLOW) to box important terms
- run_time: 1.0 for standard animations, 1.5-2.0 for complex transforms, 0.5 for minor highlights

### Anti-patterns (AI slop — never generate these)
- ❌ All objects appear at once via a single self.add() call
- ❌ FadeIn(equation) — use Write(equation)
- ❌ Objects that appear and never move, highlight, or change
- ❌ Random color per object with no semantic assignment
- ❌ self.wait(0) or missing waits between beats
- ❌ MathTex wider than frame (check .width < 10, scale if not)
- ❌ Title + 6 equations + 3 arrows all visible simultaneously
- ❌ Plain Text() for math expressions like "f(x) = x^2"
- ❌ Hardcoded pixel shifts like .shift(RIGHT * 3.14159)
- ❌ Pure BLACK (#000000) background
"""


# ── Template infrastructure ────────────────────────────────────────────────────

class ParametrizedScene(ABC):
    """A template that accepts a params dict and returns Manim CE source code."""

    name: str = ""
    description: str = ""
    params_schema: dict = {}

    @abstractmethod
    def render(self, params: dict[str, Any]) -> str:
        """Return complete Manim CE Python source string, starting with STYLE_HEADER."""
        ...

    def _header(self) -> str:
        return STYLE_HEADER

    def _scene_open(self, class_name: str) -> str:
        return f'''
class {class_name}(Scene):
    def construct(self):
        self.camera.background_color = BACKGROUND_COLOR
'''


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
