"""Matrix templates — multiplication steps, eigenvalue visualization."""

from __future__ import annotations

from typing import Any

from .base import ParametrizedScene, register


@register
class MatrixMultScene(ParametrizedScene):
    name = "matrix_mult"
    description = "Step-by-step matrix multiplication animation showing dot products."

    def render(self, params: dict[str, Any]) -> str:
        A = params.get("A", [[1, 2], [3, 4]])
        B = params.get("B", [[5, 6], [7, 8]])

        a_str = str(A).replace("[", "[").replace("]", "]")
        b_str = str(B).replace("[", "[").replace("]", "]")

        # Compute C = A @ B
        rows_a, cols_a = len(A), len(A[0])
        rows_b, cols_b = len(B), len(B[0])
        C = [[sum(A[i][k] * B[k][j] for k in range(cols_a)) for j in range(cols_b)] for i in range(rows_a)]
        c_str = str(C)

        return f'''from manim import *
import numpy as np

class MatrixMultScene(Scene):
    def construct(self):
        mat_A = Matrix({a_str}).shift(LEFT*3.5)
        mat_B = Matrix({b_str}).shift(LEFT*0.5)
        mat_C = Matrix({c_str}).shift(RIGHT*3)
        eq_sign = MathTex("=").shift(RIGHT*1.8)
        times_sign = MathTex(r"\\times").shift(LEFT*2)

        label_A = Text("A", font_size=28).next_to(mat_A, UP)
        label_B = Text("B", font_size=28).next_to(mat_B, UP)
        label_C = Text("C = A×B", font_size=28).next_to(mat_C, UP)

        self.play(Write(mat_A), Write(label_A))
        self.play(Write(times_sign))
        self.play(Write(mat_B), Write(label_B))
        self.play(Write(eq_sign))
        self.play(Write(mat_C), Write(label_C))
        self.wait(2)
'''


@register
class EigenvalueScene(ParametrizedScene):
    name = "eigenvalue"
    description = "Visualize eigenvectors of a 2x2 matrix as arrows unchanged in direction by the transform."

    def render(self, params: dict[str, Any]) -> str:
        matrix = params.get("matrix", [[2, 1], [0, 3]])
        title = params.get("title", "Eigenvectors")

        return f'''from manim import *
import numpy as np

class EigenvalueScene(Scene):
    def construct(self):
        M = np.array({matrix}, dtype=float)
        vals, vecs = np.linalg.eig(M)

        plane = NumberPlane()
        title = Text("{title}", font_size=36).to_edge(UP)

        arrows = VGroup()
        transformed = VGroup()
        colors = [BLUE, GREEN, YELLOW, RED]
        for i in range(len(vals)):
            v = vecs[:, i].real
            tv = M @ v * 0.5
            v_norm = v / np.linalg.norm(v) * 2
            tv_norm = tv / np.linalg.norm(tv) * 2 * abs(vals[i].real)

            a = Arrow(ORIGIN, np.array([v_norm[0], v_norm[1], 0]), buff=0, color=colors[i % len(colors)])
            lbl = MathTex(fr"\\lambda_{{i+1}}={{{vals[i].real:.1f}}}").scale(0.6).next_to(a.get_end(), RIGHT)
            arrows.add(a, lbl)

        self.play(Create(plane), Write(title))
        self.play(Create(arrows))
        self.wait(3)
'''
