"""Vector templates — VectorSumScene, VectorFieldScene."""

from __future__ import annotations

from typing import Any

from .base import ParametrizedScene, register


@register
class VectorSumScene(ParametrizedScene):
    name = "vector_sum"
    description = "Show 2-4 vectors being added tip-to-tail with resultant highlighted."

    def render(self, params: dict[str, Any]) -> str:
        vectors = params.get("vectors", [[1, 2], [2, -1]])
        show_components = params.get("show_components", False)
        labels = params.get("labels", [f"v_{i+1}" for i in range(len(vectors))])
        colors = ["BLUE", "GREEN", "YELLOW", "ORANGE"]

        vec_lines = []
        for i, v in enumerate(vectors):
            color = colors[i % len(colors)]
            vec_lines.append(f'    v{i} = Arrow(ORIGIN, np.array([{v[0]}, {v[1]}, 0]), buff=0, color={color})')
            vec_lines.append(f'    l{i} = MathTex(r"\\vec{{{labels[i]}}}", color={color}).scale(0.7).next_to(v{i}.get_center(), UP, buff=0.1)')

        # Tip-to-tail placement
        shift_lines = []
        cumx, cumy = 0.0, 0.0
        for i, v in enumerate(vectors):
            shift_lines.append(f'    v{i}.shift(np.array([{cumx}, {cumy}, 0]))')
            shift_lines.append(f'    l{i}.next_to(v{i}.get_center(), UP, buff=0.1)')
            cumx += v[0]
            cumy += v[1]

        resultant = f'    resultant = Arrow(ORIGIN, np.array([{cumx}, {cumy}, 0]), buff=0, color=RED)'
        res_label = f'    res_label = MathTex(r"\\vec{{R}}", color=RED).scale(0.8).next_to(resultant.get_center(), RIGHT, buff=0.1)'

        play_lines = []
        for i in range(len(vectors)):
            play_lines.append(f'    self.play(GrowArrow(v{i}), Write(l{i}))')
        play_lines.append('    self.play(GrowArrow(resultant), Write(res_label))')
        play_lines.append('    self.wait(1.5)')

        return f'''from manim import *
import numpy as np

class VectorSumScene(Scene):
    def construct(self):
{chr(10).join(vec_lines)}
{chr(10).join(shift_lines)}
{resultant}
{res_label}
{chr(10).join(play_lines)}
'''


@register
class VectorFieldScene(ParametrizedScene):
    name = "vector_field"
    description = "Show a 2D vector field with optional streamlines."

    def render(self, params: dict[str, Any]) -> str:
        fx = params.get("fx", "-y")
        fy = params.get("fy", "x")
        title = params.get("title", "Vector Field")

        return f'''from manim import *
import numpy as np

class VectorFieldScene(Scene):
    def construct(self):
        plane = NumberPlane()
        field = ArrowVectorField(
            lambda pos: np.array([{fx.replace("x", "pos[0]").replace("y", "pos[1]")},
                                   {fy.replace("x", "pos[0]").replace("y", "pos[1]")}, 0]),
            x_range=[-4, 4, 0.8],
            y_range=[-3, 3, 0.8],
        )
        title = Text("{title}", font_size=36).to_edge(UP)
        self.play(Create(plane), run_time=1)
        self.play(Create(field), run_time=2)
        self.play(Write(title))
        self.wait(2)
'''
