"""Signal/frequency templates — waveforms, FFT spectrum."""

from __future__ import annotations

from typing import Any

from .base import ParametrizedScene, register


@register
class WaveformScene(ParametrizedScene):
    name = "waveform"
    description = "Animate a waveform (single or sum of sinusoids)."

    def render(self, params: dict[str, Any]) -> str:
        components = params.get("components", [{"freq": 1, "amp": 1, "color": "BLUE"}])
        show_sum = params.get("show_sum", True)
        title = params.get("title", "Waveform")

        plot_lines = []
        colors = ["BLUE", "GREEN", "YELLOW"]
        sum_expr_parts = []
        for i, c in enumerate(components):
            freq = c.get("freq", 1)
            amp = c.get("amp", 1)
            color = c.get("color", colors[i % len(colors)])
            label = c.get("label", f"{amp}·sin({freq}t)")
            sum_expr_parts.append(f'{amp}*np.sin({freq}*x)')
            plot_lines.append(
                f'        g{i} = axes.plot(lambda x: {amp}*np.sin({freq}*x), color={color})'
            )
            plot_lines.append(
                f'        self.play(Create(g{i}), run_time=1.5)'
            )

        if show_sum and len(components) > 1:
            sum_expr = " + ".join(sum_expr_parts)
            plot_lines.append(f'        g_sum = axes.plot(lambda x: {sum_expr}, color=RED, stroke_width=3)')
            plot_lines.append("        self.play(Create(g_sum), run_time=1.5)")

        return f'''from manim import *
import numpy as np

class WaveformScene(Scene):
    def construct(self):
        axes = Axes(
            x_range=[0, 4*PI, PI/2],
            y_range=[-3, 3, 1],
            axis_config={{"color": WHITE}},
            tips=False,
        )
        title = Text("{title}", font_size=36).to_edge(UP)
        self.play(Create(axes), Write(title))
{chr(10).join(plot_lines)}
        self.wait(2)
'''


@register
class FrequencySpectrumScene(ParametrizedScene):
    name = "frequency_spectrum"
    description = "Show FFT frequency spectrum as vertical bars with highlighted peaks."

    def render(self, params: dict[str, Any]) -> str:
        frequencies = params.get("frequencies", [1, 2, 3, 4, 5])
        amplitudes = params.get("amplitudes", [0.9, 0.3, 0.5, 0.1, 0.2])
        title = params.get("title", "Frequency Spectrum")

        return f'''from manim import *
import numpy as np

class FrequencySpectrumScene(Scene):
    def construct(self):
        freqs = {frequencies}
        amps = {amplitudes}
        title = Text("{title}", font_size=36).to_edge(UP)

        axes = Axes(
            x_range=[0, max(freqs)+1, 1],
            y_range=[0, max(amps)*1.3, 0.2],
            axis_config={{"color": WHITE}},
            tips=False,
        )
        x_labels = axes.get_x_axis().add_labels({{f: f"{{f}}Hz" for f in freqs}})

        bars = VGroup()
        for f, a in zip(freqs, amps):
            bar = Rectangle(
                width=0.4,
                height=axes.y_axis.get_unit_size() * a,
                fill_color=BLUE,
                fill_opacity=0.8,
                stroke_color=WHITE,
            ).move_to(axes.c2p(f, a/2))
            bars.add(bar)

        self.play(Write(title), Create(axes))
        self.play(LaggedStart(*[GrowFromEdge(b, DOWN) for b in bars], lag_ratio=0.1))
        self.wait(2)
'''
