"""Geometry templates — polygon transforms, circle theorems."""

from __future__ import annotations

from typing import Any

from .base import ParametrizedScene, register


@register
class PolygonTransformScene(ParametrizedScene):
    name = "polygon_transform"
    description = "Show a polygon being transformed (rotate, scale, reflect) step by step."

    def render(self, params: dict[str, Any]) -> str:
        n_sides = params.get("n_sides", 3)
        transforms = params.get("transforms", [{"type": "rotate", "angle": 60}, {"type": "scale", "factor": 1.5}])
        color = params.get("color", "BLUE")

        transform_lines = []
        for t in transforms:
            if t["type"] == "rotate":
                angle = t.get("angle", 45)
                transform_lines.append(f'        self.play(Rotate(poly, angle=PI*{angle}/180, about_point=ORIGIN))')
            elif t["type"] == "scale":
                factor = t.get("factor", 2)
                transform_lines.append(f'        self.play(poly.animate.scale({factor}))')
            elif t["type"] == "reflect":
                axis = t.get("axis", "x")
                flip = "[[1,0,0],[0,-1,0],[0,0,1]]" if axis == "x" else "[[-1,0,0],[0,1,0],[0,0,1]]"
                transform_lines.append(f'        self.play(poly.animate.apply_matrix(np.array({flip})))')
            transform_lines.append('        self.wait(0.5)')

        return f'''from manim import *
import numpy as np

class PolygonTransformScene(Scene):
    def construct(self):
        poly = RegularPolygon(n={n_sides}, color={color}).scale(2)
        self.play(Create(poly))
        self.wait(0.5)
{chr(10).join(transform_lines)}
        self.wait(1)
'''


@register
class CircleScene(ParametrizedScene):
    name = "circle_theorem"
    description = "Demonstrate a circle theorem with labeled points and angles."

    def render(self, params: dict[str, Any]) -> str:
        theorem = params.get("theorem", "inscribed_angle")
        title = params.get("title", "Inscribed Angle Theorem")

        return f'''from manim import *
import numpy as np

class CircleScene(Scene):
    def construct(self):
        circle = Circle(radius=2, color=WHITE)
        title = Text("{title}", font_size=36).to_edge(UP)

        # Center and points on circle
        O = Dot(ORIGIN, color=YELLOW)
        A = Dot(circle.point_at_angle(PI/6), color=BLUE)
        B = Dot(circle.point_at_angle(5*PI/6), color=BLUE)
        C = Dot(circle.point_at_angle(3*PI/2), color=RED)

        arc = Arc(radius=2, start_angle=PI/6, angle=4*PI/6, color=GREEN)

        self.play(Create(circle), Write(title))
        self.play(FadeIn(O), FadeIn(A), FadeIn(B), FadeIn(C))
        self.play(Create(Line(A.get_center(), C.get_center())),
                  Create(Line(B.get_center(), C.get_center())))
        self.play(Create(arc))
        self.wait(2)
'''
