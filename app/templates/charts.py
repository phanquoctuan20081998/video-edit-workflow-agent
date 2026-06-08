"""Chart templates — bar charts, number lines."""

from __future__ import annotations

from typing import Any

from .base import ParametrizedScene, register


@register
class BarChartScene(ParametrizedScene):
    name = "bar_chart"
    description = "Animated bar chart with labeled categories and values."

    def render(self, params: dict[str, Any]) -> str:
        values = params.get("values", [3, 5, 2, 7, 4])
        labels = params.get("labels", [f"x{i+1}" for i in range(len(values))])
        title = params.get("title", "Bar Chart")
        bar_colors = params.get("colors", ["BLUE"] * len(values))

        return f'''from manim import *

class BarChartScene(Scene):
    def construct(self):
        chart = BarChart(
            values={values},
            bar_names={labels},
            bar_colors={bar_colors},
            y_range=[0, {max(values) * 1.2:.0f}, 1],
            y_length=5,
            x_length=9,
        )
        title = Text("{title}", font_size=36).to_edge(UP)
        self.play(Write(title))
        self.play(Create(chart))
        self.wait(2)
'''


@register
class NumberLineScene(ParametrizedScene):
    name = "number_line"
    description = "Number line with highlighted points and optional intervals."

    def render(self, params: dict[str, Any]) -> str:
        x_range = params.get("x_range", [-5, 5, 1])
        points = params.get("points", [])
        intervals = params.get("intervals", [])
        title = params.get("title", "")

        point_lines = []
        for p in points:
            val = p.get("value", 0)
            label = p.get("label", str(val))
            color = p.get("color", "RED")
            point_lines.append(f'        dot = Dot(nl.n2p({val}), color={color})')
            point_lines.append(f'        lbl = MathTex(r"{label}").next_to(nl.n2p({val}), UP)')
            point_lines.append(f'        self.play(FadeIn(dot), Write(lbl))')

        interval_lines = []
        for iv in intervals:
            a, b = iv.get("start", 0), iv.get("end", 1)
            color = iv.get("color", "YELLOW")
            interval_lines.append(f'        seg = Line(nl.n2p({a}), nl.n2p({b}), color={color}, stroke_width=6)')
            interval_lines.append(f'        self.play(Create(seg))')

        title_line = f'        title = Text("{title}", font_size=32).to_edge(UP)' if title else ''
        title_play = '        self.play(Write(title))' if title else ''

        return f'''from manim import *

class NumberLineScene(Scene):
    def construct(self):
        nl = NumberLine(x_range={x_range}, length=10, include_numbers=True)
{title_line}
{title_play}
        self.play(Create(nl))
{chr(10).join(interval_lines)}
{chr(10).join(point_lines)}
        self.wait(2)
'''
