"""Function graph templates."""

from __future__ import annotations

from typing import Any

from .base import ParametrizedScene, register


@register
class FunctionGraphScene(ParametrizedScene):
    name = "function_graph"
    description = "Plot one or more functions on labeled axes with optional highlight region."

    def render(self, params: dict[str, Any]) -> str:
        functions = params.get("functions", [{"expr": "np.sin(x)", "label": "sin(x)", "color": "BLUE"}])
        x_range = params.get("x_range", [-4, 4])
        y_range = params.get("y_range", [-2, 2])
        title = params.get("title", "")
        highlight = params.get("highlight_region", None)

        plot_lines = []
        colors = ["BLUE", "GREEN", "YELLOW", "RED"]
        for i, fn in enumerate(functions):
            color = fn.get("color", colors[i % len(colors)])
            label = fn.get("label", f"f_{i}")
            expr = fn.get("expr", "x")
            plot_lines.append(f'        g{i} = axes.plot(lambda x: {expr}, color={color})')
            plot_lines.append(f'        lbl{i} = axes.get_graph_label(g{i}, label=r"{label}", x_val={x_range[1]*0.7:.1f})')

        play_lines = [f'        self.play(Create(axes), Write(labels))']
        for i in range(len(functions)):
            play_lines.append(f'        self.play(Create(g{i}), Write(lbl{i}))')

        if highlight:
            a, b = highlight
            play_lines.append(f'        region = axes.get_area(g0, x_range=[{a}, {b}], color=YELLOW, opacity=0.3)')
            play_lines.append(f'        self.play(FadeIn(region))')

        play_lines.append('        self.wait(2)')
        title_line = f'        title = Text("{title}", font_size=36).to_edge(UP)' if title else ''
        title_play = '        self.play(Write(title))' if title else ''

        return f'''from manim import *
import numpy as np

class FunctionGraphScene(Scene):
    def construct(self):
        axes = Axes(
            x_range=[{x_range[0]}, {x_range[1]}, 1],
            y_range=[{y_range[0]}, {y_range[1]}, 1],
            axis_config={{"color": WHITE}},
        )
        labels = axes.get_axis_labels(x_label="x", y_label="y")
{title_line}
{title_play}
{chr(10).join(plot_lines)}
{chr(10).join(play_lines)}
'''


@register
class DerivativeTangentScene(ParametrizedScene):
    name = "derivative_tangent"
    description = "Show a function with an animated tangent line demonstrating the derivative."

    def render(self, params: dict[str, Any]) -> str:
        expr = params.get("expr", "x**2")
        x_start = params.get("x_start", -2.0)
        x_end = params.get("x_end", 2.0)
        tangent_at = params.get("tangent_at", 1.0)

        return f'''from manim import *
import numpy as np

class DerivativeTangentScene(Scene):
    def construct(self):
        axes = Axes(x_range=[-3, 3, 1], y_range=[-1, 5, 1])
        graph = axes.plot(lambda x: {expr}, color=BLUE, x_range=[{x_start}, {x_end}])
        graph_label = axes.get_graph_label(graph, label=r"f(x)", x_val={x_end*0.8:.1f})

        dot = Dot(axes.i2gp({tangent_at}, graph), color=RED)
        tangent = axes.get_secant_slope_group(
            x={tangent_at}, graph=graph, dx=0.01,
            secant_line_color=YELLOW, secant_line_length=4,
        )

        self.play(Create(axes), Create(graph), Write(graph_label))
        self.play(FadeIn(dot))
        self.play(Create(tangent))
        self.wait(2)
'''
