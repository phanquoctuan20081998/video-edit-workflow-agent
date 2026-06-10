"""Stage 3 — Manim codegen agent with self-repair loop.

generate → sandbox_exec → [repair loop] → visual QA

Two distinct repair paths:
  1. runtime_error  → pass traceback + history, fix syntax/API errors
  2. visual QA fail → pass frame screenshots + issues + history, fix visual layout

Repair improvements over v1:
  - History context: each repair call sees all previous failed attempts
  - Temperature escalation: 0.2 → 0.5 → 0.7 on repeated failures
  - Code-change validation: detect identical LLM responses, force re-attempt
  - Error triage: classify error type → inject targeted hint before repair
  - Phase-based QA repair: correctness issues first, style issues second
  - Extended pre-checks: missing self.play(), beat markers, hardcoded shifts
  - Simplified fallback: last-resort simpler scene before human review

Cache: checks manim_code_hash before any exec.
Cap: raises RepairCapExceeded after max_repairs attempts.
"""

from __future__ import annotations

import ast
import os
import shutil
import traceback as traceback_module
from dataclasses import dataclass
from pathlib import Path

import structlog

from app.agents.visual_qa import QAResult, vision_qa
from app.config import get_settings
from app.models.video_spec import Scene, VideoSpec
from app.providers.base import LLMMessage
from app.providers.factory import get_llm_provider
from app.sandbox.frame_sampler import motion_score, sample_frames
from app.sandbox.runner import SandboxResult, sandbox_exec

log = structlog.get_logger()

_CODEGEN_MAX_TOKENS = 12000

# Mean inter-frame pixel difference (0-255) below which a clip is treated as a
# static slide and auto-failed without spending a vision-QA call.
_MIN_MOTION_SCORE = 1.5

# Temperature escalates on repeated failures to escape local minima.
_REPAIR_TEMPS = [0.2, 0.2, 0.5, 0.7]

# Map exception type → targeted repair hint injected before the error traceback.
_ERROR_HINTS: dict[str, str] = {
    "AttributeError": "Check Manim CE API — method/attribute may not exist on this mobject class.",
    "ValueError": "Check argument types and value ranges for Manim constructors.",
    "NameError": "Undefined name — ensure palette constants (P_BLUE etc.) are defined above the class.",
    "TypeError": "Check argument count and types for Manim constructors and methods.",
    "IndexError": "List/array index out of range — check loop bounds and VGroup sizes.",
    "ZeroDivisionError": "Division by zero — guard denominators (e.g. normalize only non-zero vectors).",
    "'opacity'": "VMobject does not accept opacity= as a constructor kwarg. Use fill_opacity= for fill opacity, stroke_opacity= for border opacity, or call .set_opacity(val) after creation.",
    "ShowCreation": "ShowCreation is manimlib (old). In Manim CE use Create(...).",
    "TextMobject": "TextMobject/TexMobject are manimlib (old). In Manim CE use Text(...) and MathTex(...).",
    "get_graph": "axes.get_graph() is manimlib (old). In Manim CE use axes.plot(lambda x: ..., color=...).",
    "GraphScene": "GraphScene is manimlib (old). In Manim CE subclass Scene and create Axes(...) manually.",
    "CONFIG": "The CONFIG dict pattern is manimlib (old). In Manim CE pass options to __init__ or set them in construct().",
    "ModuleNotFoundError": "Only manim and numpy are available in the sandbox. Remove other imports.",
    "ImportError": "Only manim and numpy are available in the sandbox. Remove other imports.",
    "FileNotFoundError": "No file I/O outside /workspace is allowed.",
    "LatexError": "Simplify MathTex — use only ASCII LaTeX. Remove Unicode and non-ASCII characters.",
}

_GENERATE_SYSTEM = """\
You are an expert Manim Community Edition (CE) developer generating math/physics explainer
animations in the style of 3Blue1Brown. Your output must be visually clean, mathematically
purposeful, and never "AI slop" (random colors, wall-of-text reveals, static objects).

═══ API RULES ═══
- Manim CE only (not manimlib). Always start with the STYLE_HEADER block below.
- One Scene subclass per file, class name matches the concept.
- Use MovingCameraScene, not Scene, if animating self.camera.frame for zoom/pan.
- Never animate self.camera directly. Use self.camera.frame.animate... in MovingCameraScene.
- No network imports, no file I/O outside /workspace.
- Total animation ≤ 80 seconds. Use run_time= to control pacing.
- Do not pass num_points= to Manim mobjects. For curves, use ParametricFunction with
  t_range or create points yourself.
- Do NOT pass opacity= to any Mobject constructor — VMobject rejects it at runtime.
  Use fill_opacity= for fill, stroke_opacity= for border, or .set_opacity(val) after
  creation. Example: Circle(fill_opacity=0.5) not Circle(opacity=0.5).
- Keep grids/matrices ≤ 6×6 cells. Do not try to draw exact pixel-count connection
  lines or arrows (e.g. 64 arrows) — approximate with representative arrows instead.
- At most 5–6 objects visible per beat. FadeOut old objects before adding new ones
  if count would exceed this limit.

═══ STYLE HEADER (copy verbatim at top of every file) ═══
from manim import *
import numpy as np

BACKGROUND_COLOR = "#1C1C2E"
P_BLUE   = "#58C4DD"   # primary objects
P_GREEN  = "#58A162"   # secondary objects (muted forest green, not chartreuse)
P_YELLOW = "#FFFF00"   # emphasis / final answer
P_GOLD   = "#C49A04"   # softer emphasis
P_RED    = "#FC6255"   # negation / error
P_TEAL   = "#49A88F"   # transforms / in-between
P_WHITE  = "#FFFFFF"   # text, formulas
P_GREY   = "#BDBDBD"   # secondary labels
P_AXIS   = "#1C758A"   # axes, grids (subdued)
P_DIM    = "#55534E"   # dashed guides, minor elements

First line of construct(): self.camera.background_color = BACKGROUND_COLOR

═══ COLOR SEMANTICS ═══
FORBIDDEN COLORS: Never use chartreuse, lime, neon green, or any yellow-green.
P_GREEN (#58A162) is a muted forest green — use it as-is, never brighten it.
Assign ONE meaning per color, keep it for the entire scene:
  P_BLUE   → primary mathematical object (vector, curve, key shape)
  P_GREEN  → secondary / supporting object
  P_YELLOW → final result, answer, or peak emphasis (use sparingly)
  P_RED    → negation, cancellation, what's being removed
  P_AXIS   → NumberPlane, Axes, grid (never dominant)
  P_DIM    → DashedLine, construction aids
  P_WHITE  → ALL text and MathTex
NEVER assign colors arbitrarily. Viewer infers: same color = same concept.
Manim color helpers need ManimColor objects. If using interpolate_color with palette
constants, write: interpolate_color(ManimColor(P_BLUE), ManimColor(P_GREEN), alpha).

═══ TYPOGRAPHY ═══
- MathTex for ALL math. Never: Text("f(x) = x²") — always: MathTex(r"f(x) = x^2")
- MathTex strings must contain ASCII LaTeX only. Never put Vietnamese, Unicode prose,
  or natural-language labels inside MathTex or \text{...}; use Text(...) for prose labels
  and place a separate MathTex(...) next to it for formulas.
  Bad: MathTex(r"T_j \\text{ xử lý } k : 2^j \\le |k| < 2^{j+1}")
  Good: VGroup(MathTex(r"T_j"), Text("xử lý"), MathTex(r"k : 2^j \\le |k| < 2^{j+1}"))
- After creating MathTex, check width: if tex.width > 10: tex.scale(10 / tex.width)
- Never include citations or bibliography commands in rendered scenes: no \\cite,
  \\bibitem, \\bibliography, \\begin{thebibliography}, or paper-reference fragments.
- For long norms/suprema, split into 2-3 short MathTex lines rather than one giant
  expression. A readable schematic plus one key formula is better than a paper excerpt.
- Title: Text("title", font_size=40, color=P_WHITE).to_edge(UP, buff=0.5)
- Labels: scale(0.65) relative to main objects, next_to(obj, direction, buff=0.25)

═══ LAYOUT ═══
- No overlaps. Stack with: VGroup(a, b, c).arrange(DOWN, buff=0.75)
- Position with to_edge(), next_to(), move_to() — NEVER hardcode .shift(3.14)
- Margin: nothing within 0.5 units of frame edge (frame = 14.22 × 8.0 units)
- Max 6–8 objects visible simultaneously. More → split or FadeOut old ones.
- Prefer a simple 2D schematic over a complex 3D construction when the concept can be
  taught schematically. A clean morphing boundary beats a cluttered pseudo-3D scene.
- For color fields, matrices, or dot clouds: include a tiny legend with 2-3 labels, and
  keep the same notation everywhere. If text says A(x,t), matrix entries must be a_ij.
- Do not show an equation, a matrix, and prose explanation all at once. Stage them:
  reveal shape/context, then formula, then matrix/field, then conclusion.
- If a visual intent says "morphs from X to Y", actually create X first, then Transform
  it into Y before adding labels or formulas.

═══ ANIMATION RHYTHM ═══
This is the most critical rule. Each play() call = one idea.

  Write(tex)                  — formulas appear stroke-by-stroke (always use for math)
  Create(shape)               — shapes drawn along path
  FadeIn(obj)                 — background/context only, NEVER for hero math
  Transform(A, B)             — A becomes B (shows mathematical equivalence)
  ReplacementTransform(A, B)  — A consumed, becomes B
  Indicate(obj)               — pulse to focus attention without moving
  Circumscribe(obj)           — draw circle around to highlight
  SurroundingRectangle(obj, color=P_YELLOW)  — box a key term

Pacing:
  self.wait(1.0)   after every major reveal
  self.wait(0.5)   between minor beats
  run_time=1.0     standard; 1.5–2.0 for complex transforms; 0.5 for minor highlights
  NEVER self.wait(0)

═══ POLISH (what makes it feel hand-crafted, not generated) ═══
- Group reveals: use LaggedStart(*[Create(d) for d in dots], lag_ratio=0.1) instead of
  revealing many similar objects in one pop.
- Movement easing: default rate_func is fine; for sweeping motion use rate_func=smooth.
- Keep the title on screen for the whole scene; everything else enters/leaves below it.
- When replacing content, FadeOut(old, shift=DOWN*0.3) then bring the new in — never
  leave orphaned objects behind a new layout.
- Axes: always pass axis_config={"color": P_AXIS, "stroke_width": 2} so grids stay subdued.
- One focal point per moment: when a formula is the focus, dim other objects with
  .animate.set_opacity(0.3), then restore.

═══ GOLDEN MINI-EXAMPLE (rhythm + style reference — adapt, don't copy verbatim) ═══
class UnitCircleSine(Scene):
    def construct(self):
        self.camera.background_color = BACKGROUND_COLOR
        title = Text("Where sine comes from", font_size=40, color=P_WHITE).to_edge(UP, buff=0.5)
        self.play(Write(title)); self.wait(0.5)
        ax = Axes(x_range=[0, 7, 1], y_range=[-1.5, 1.5, 1], x_length=6, y_length=3,
                  axis_config={"color": P_AXIS, "stroke_width": 2}).to_edge(RIGHT, buff=0.8)
        circle = Circle(radius=1.2, color=P_BLUE).to_edge(LEFT, buff=1.2)
        self.play(Create(circle), run_time=1.0)
        self.play(Create(ax), run_time=1.0); self.wait(1.0)
        dot = Dot(circle.point_at_angle(0), color=P_YELLOW)
        radius = Line(circle.get_center(), dot.get_center(), color=P_GREEN)
        self.play(FadeIn(dot), Create(radius)); self.wait(0.5)
        curve = ax.plot(lambda x: np.sin(x), color=P_BLUE)
        label = MathTex(r"y = \\sin(\\theta)", color=P_WHITE).scale(0.8).next_to(ax, UP, buff=0.25)
        self.play(Create(curve), Write(label), run_time=2.0)
        self.play(Indicate(dot)); self.wait(1.0)

═══ ANTI-PATTERNS — NEVER GENERATE ═══
❌ self.add(obj1, obj2, obj3, obj4) — all at once, no animation
❌ FadeIn(equation) — use Write(equation)
❌ Objects that appear and never interact, move, or change color
❌ Random color per object (BLUE, GREEN, RED, ORANGE all in one scene with no meaning)
❌ Unexplained colored dots, fields, or heat maps
❌ Notation mismatch such as A(x,t) in prose but b_ij in the displayed matrix
❌ Dense single-frame summaries with shape + matrix + equation + paragraph together
❌ Missing waits between reveals
❌ MathTex wider than 10 units (always check .width)
❌ Bibliography/citation commands or copied paper fragments in MathTex
❌ Text() for math expressions
❌ Hardcoded shifts: .shift(RIGHT * 3.14159)
❌ Pure black background: background_color = BLACK
❌ Showing the entire derivation in one screen with 8+ equations simultaneously
"""

_FALLBACK_SYSTEM = """\
Generate the SIMPLEST possible Manim scene that illustrates the concept visually.
The regular generation failed multiple times — prioritize a scene that will render
successfully over one that is visually complex.

Constraints:
- Maximum 3–4 objects on screen at any time.
- Exactly one Transform or ReplacementTransform (required).
- Use plain Scene, NOT MovingCameraScene — no camera animation.
- No MathTex wider than 6 units (scale down if needed).
- No VGroup with more than 3 elements.
- No nested animations or complex rate_funcs.
- Still use the palette constants and dark background.
- Output ONLY the Python source code, no markdown fences.
"""

_GENERATE_PROMPT = """\
Create a Manim CE scene for the following animation:

Visual intent: {visual_spec}
Narration context: {narration}
Language: {language}
{beats_section}
Design target:
- Build a teachable animation, not a static summary slide.
- Use 3-5 short visual moments: context object -> change/transform -> formula/label -> conclusion.
- Put at most one substantial formula on screen at a time.
- Prefer geometric objects, arrows, meters, highlighted regions, and transforms over paragraphs.
- Every scene must include at least one Create(...) and at least one Transform(...),
  ReplacementTransform(...), or object .animate movement.

Complexity limits (HARD RULES — violations will be rejected):
- Grids: at most 6×6 cells. Do not generate 8×8 or larger grids.
- Connection lines / arrows: show at most 8 representative arrows, not every possible connection.
- Objects per beat: at most 5–6 on screen simultaneously. FadeOut old ones before adding new.
- Accuracy meters / gauges: use a simple Arc + needle (no pixel-precise gauge graphics).
- Do NOT show an accuracy needle dropping to a specific percentage — use relative motion only.

YOUR RESPONSE MUST FOLLOW THIS EXACT SKELETON — no exceptions:

from manim import *
import numpy as np

BACKGROUND_COLOR = "#1C1C2E"
P_BLUE = "#58C4DD"
# ... other palette constants ...

class SceneName(Scene):
    def construct(self):
        self.camera.background_color = BACKGROUND_COLOR
        # animation code here

Output ONLY the Python source code. No markdown fences. No prose. No explanation.
The top-level class MUST inherit from Scene.
"""

_GENERATE_BEATS_SECTION = """\

═══ BEAT STRUCTURE ═══
This scene has {n_beats} sequential beats. Generate ONE continuous construct() method
where each beat is a labeled section. Objects persist across beats — use Transform,
not Create+FadeOut for object changes.

Mark each beat with a comment: # ═══ BEAT {beat_id}: {visual_action} ═══
After each beat's animations, add: self.wait(1.0)  # beat boundary

Beats (in order):
{beats_list}

For every beat:
- The animation must directly illustrate the exact Narration segment for that beat.
- The beat's animations + waits should sum to roughly its "Target animation time".
  Matching this keeps the animation in sync with the voiceover — segments that are
  far off get speed-warped in compositing, which looks bad.
- Show every item in Must show, using visible geometry, arrows, highlights, transforms,
  labels, or formulas.
- Include the On-screen label when present, but keep it compact.
- Avoid every item in Forbidden visuals.
- Do not use a generic summary visual when the beat asks for a specific object/change.

CRITICAL: The animation must be continuous. Objects created in beat 1 should still be
visible/transformable in beat 3. Only FadeOut objects when the narration explicitly
moves past them. The self.wait() between beats is where the compositor will cut if using
split-render strategy, or where timing will be adjusted.
"""

_REPAIR_RUNTIME_PROMPT = """\
The following Manim CE code produced a runtime error. Fix it.
{history}
=== CURRENT CODE ===
{code}

=== ERROR TRACEBACK ===
{traceback}
{hint}
{force_note}
Output ONLY the fixed Python source code, no explanations, no markdown fences.
"""

_REPAIR_VISUAL_PROMPT = """\
The following Manim CE code rendered successfully but has visual problems. Fix the visual issues.

=== ORIGINAL VISUAL INTENT ===
{visual_spec}

=== NARRATION CONTEXT ===
{narration}

=== SCENE BEATS ===
{beats}
{history}
=== CURRENT CODE ===
{code}

=== {phase_label} (address these FIRST) ===
{priority_issues}

=== ALL KNOWN ISSUES ===
{all_issues}

{phase_note}

Repair strategy:
- You may restructure the scene, not just patch labels.
- If the current scene is a static slide, rebuild it as 3-5 sequential visual moments.
- Remove or split crowded groups until no frame has more than 6-8 visible objects.
- Replace garbled or risky MathTex with simpler ASCII LaTeX, or with Text labels plus
  separate MathTex formulas.
- Keep notation consistent across labels, matrices, and formulas.
- Add legends for any color-coded dots/fields.
- If a requested transformation is missing, add it early and make it visually obvious.
- Preserve the original visual intent and beat order above. Do not invent unrelated math.
- For every beat, make the frames visibly satisfy its Narration and Must show items.
- Remove visuals listed under Forbidden visuals.
- Every repaired scene must include at least one Create(...) and at least one Transform(...),
  ReplacementTransform(...), or object .animate movement.
{force_note}
Output ONLY the fixed Python source code, no explanations, no markdown fences.
"""


class RepairCapExceeded(Exception):
    """Raised when a scene exhausts all repair attempts."""


@dataclass
class RenderResult:
    success: bool
    clip_path: str | None
    qa_passed: bool
    code: str
    attempts: int
    flagged_for_human: bool = False
    error: str | None = None


from collections.abc import Callable
ProgressCb = Callable[[str, str], None]   # (scene_id, message) -> None
LogCb = Callable[[str], None]             # (log_line) -> None


async def render_scene(
    scene: Scene,
    spec: VideoSpec,
    artifact_dir: str | None = None,
    max_repairs: int = 4,
    n_variants: int = 1,
    progress_cb: ProgressCb | None = None,
    log_cb: LogCb | None = None,
) -> RenderResult:
    """Generate + exec + repair + QA a single scene. Updates scene in-place on success."""
    cfg = get_settings()
    base_dir = artifact_dir or cfg.artifact_dir
    out_dir = os.path.join(base_dir, spec.project_id, "scenes", scene.id)
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    import time as _time

    def _emit(msg: str) -> None:
        if progress_cb:
            progress_cb(scene.id, msg)

    def _log(msg: str) -> None:
        if log_cb:
            ts = _time.strftime("%H:%M:%S")
            log_cb(f"[{ts}] {msg}")

    # Cache check
    cached = _check_cache(scene, out_dir)
    if cached:
        log.info("manim_codegen.cache_hit", scene_id=scene.id, hash=scene.manim_code_hash)
        scene.set_clip(cached, qa_passed=True)
        if scene.has_beats and scene.manim_code and not scene.beat_render_durations:
            durs = _extract_beat_durations_from_code(scene.manim_code, len(scene.beats))
            if durs:
                scene.beat_render_durations = durs
        _emit("✅ cache hit")
        _log(f"[{scene.id}] Cache hit — skipping render")
        return RenderResult(success=True, clip_path=cached, qa_passed=True, code=scene.manim_code, attempts=0)

    llm = get_llm_provider()
    best_result: RenderResult | None = None

    for variant in range(n_variants):
        _emit(f"🤖 generating code (variant {variant + 1}/{n_variants})…")
        _log(f"[{scene.id}] Sending visual_spec + {len(scene.beats)} beats to LLM for code generation…")
        _log(f"[{scene.id}] visual_spec: {scene.visual_spec[:120]}…")
        log.info("manim_codegen.generate", scene_id=scene.id, variant=variant)
        code = await _generate_code(llm, scene, spec)
        n_lines = len(code.splitlines())
        _log(f"[{scene.id}] LLM returned {n_lines} lines of Python")
        total_attempts = 0
        repair_history: list[dict] = []

        for attempt in range(max_repairs + 1):
            total_attempts += 1
            _emit(f"🏃 sandbox attempt {attempt + 1}/{max_repairs + 1}…")
            _log(f"[{scene.id}] Running pre-checks + sandbox (attempt {attempt + 1})…")
            log.info("manim_codegen.exec", scene_id=scene.id, attempt=attempt)

            # Programmatic fix: if Scene subclass is missing, wrap before any check.
            # LLM is unreliable at adding a class header — do it ourselves.
            if not _has_scene_subclass(code):
                wrapped = _ensure_scene_subclass(code)
                if _has_scene_subclass(wrapped):
                    log.info("manim_codegen.autowrap_applied", scene_id=scene.id, attempt=attempt)
                    _log(f"[{scene.id}] Auto-wrapped missing Scene subclass (no LLM round-trip)")
                    code = wrapped

            scene.set_manim_code(code)
            sandbox_result = (
                _syntax_check(code)
                or _scene_subclass_check(code)
                or _manimlib_pattern_check(code)
                or _undefined_name_check(code)
                or _latex_source_check(code)
                or _mathtex_incomplete_check(code)
                or _missing_play_check(code)
                or _beat_marker_check(code, scene)
                or _hardcoded_shift_check(code)
                or sandbox_exec(code, output_dir=out_dir)
            )

            if not sandbox_result.success:
                error_text = _short_error(sandbox_result)
                _log(f"[{scene.id}] ❌ {sandbox_result.error_type}:\n{error_text}")
                repair_history.append({
                    "error_type": sandbox_result.error_type,
                    "summary": error_text[:200],
                })
                if attempt == max_repairs:
                    scene.set_manim_code(code)
                    _emit("❌ repair cap reached — flagged for human review")
                    _log(f"[{scene.id}] Repair cap ({max_repairs}) reached. Flagging for human.")
                    log.warning(
                        "manim_codegen.repair_cap_runtime",
                        scene_id=scene.id,
                        error_type=sandbox_result.error_type,
                        error=error_text,
                    )
                    best_result = RenderResult(
                        success=False,
                        clip_path=None,
                        qa_passed=False,
                        code=code,
                        attempts=total_attempts,
                        flagged_for_human=True,
                        error=error_text,
                    )
                    break
                _emit(f"🔧 repairing {sandbox_result.error_type} (attempt {attempt + 1})…")
                _log(f"[{scene.id}] Sending error + code to LLM for repair (attempt {attempt + 1})…")
                log.info(
                    "manim_codegen.repair_runtime",
                    scene_id=scene.id,
                    error_type=sandbox_result.error_type,
                    error=error_text,
                )
                prev_code = code
                code = await _repair_runtime(llm, code, sandbox_result, repair_history, attempt)
                if code.strip() == prev_code.strip():
                    _log(f"[{scene.id}] ⚠️ identical code returned — force-diff retry")
                    log.warning("manim_codegen.identical_code_runtime", scene_id=scene.id, attempt=attempt)
                    code = await _repair_runtime(llm, prev_code, sandbox_result, repair_history, attempt, force_diff=True)
                _log(f"[{scene.id}] Repaired code: {len(code.splitlines())} lines")
                continue

            _log(f"[{scene.id}] ✅ Sandbox OK — sampling frames for visual QA…")
            _emit("👁️ visual QA…")
            frames = sample_frames(sandbox_result.clip_path, n=4, output_dir=os.path.join(out_dir, "frames"))

            # Cheap programmatic gate: catch static-slide slop without a vision call.
            motion = motion_score(frames)
            if 0.0 <= motion < _MIN_MOTION_SCORE:
                _log(f"[{scene.id}] ⚠️ Static scene detected (motion={motion:.2f}) — skipping vision QA")
                qa = QAResult(
                    passed=False,
                    issues=[
                        f"Animation is effectively static (frame motion score {motion:.2f}/255). "
                        "Sampled frames are nearly identical — this reads as a slide, not an animation.",
                        "Add real visual change: Create shapes sequentially, Transform one object "
                        "into another, move objects with .animate, and spread reveals across time.",
                    ],
                    correctness_issues=[],
                    style_issues=["Static slide — no meaningful animation between sampled frames."],
                )
            else:
                _log(f"[{scene.id}] Sending {len(frames)} frames to vision model…")
                qa = await vision_qa(
                    frames,
                    intent=scene.visual_spec,
                    narration=scene.narration,
                    beats=_format_scene_beats(scene),
                )

            if qa.passed:
                dest = _save_clip(sandbox_result.clip_path, out_dir, scene.id)
                scene.set_manim_code(code)
                scene.set_clip(dest, qa_passed=True)
                if scene.has_beats:
                    durs = _extract_beat_durations_from_code(code, len(scene.beats))
                    if durs:
                        scene.beat_render_durations = durs
                _emit(f"✅ done ({total_attempts} attempt{'s' if total_attempts > 1 else ''})")
                _log(f"[{scene.id}] ✅ QA passed. Clip saved to {dest}")
                log.info("manim_codegen.success", scene_id=scene.id, attempts=total_attempts)
                return RenderResult(success=True, clip_path=dest, qa_passed=True, code=code, attempts=total_attempts)

            _log(f"[{scene.id}] ⚠️ QA failed: {'; '.join(qa.issues)}")
            repair_history.append({
                "error_type": "visual_qa",
                "summary": ("; ".join(qa.issues[:3]))[:200],
            })

            if _is_qa_infrastructure_error(qa.issues):
                dest = _save_clip(sandbox_result.clip_path, out_dir, scene.id)
                _emit("⚠️ QA infrastructure error — saved, flagged for review")
                best_result = RenderResult(
                    success=True, clip_path=dest, qa_passed=False, code=code,
                    attempts=total_attempts, flagged_for_human=True,
                    error="\n".join(qa.issues),
                )
                break

            if attempt == max_repairs:
                dest = _save_clip(sandbox_result.clip_path, out_dir, scene.id)
                best_result = RenderResult(
                    success=True, clip_path=dest, qa_passed=False, code=code,
                    attempts=total_attempts, flagged_for_human=True,
                )
                break

            log.info("manim_codegen.repair_visual", scene_id=scene.id, issues=qa.issues)
            prev_code = code
            code = await _repair_visual(llm, code, qa, scene, repair_history, attempt)
            if code.strip() == prev_code.strip():
                _log(f"[{scene.id}] ⚠️ identical code returned — force-diff retry")
                log.warning("manim_codegen.identical_code_visual", scene_id=scene.id, attempt=attempt)
                code = await _repair_visual(llm, prev_code, qa, scene, repair_history, attempt, force_diff=True)

    # Before flagging for human: try a simplified fallback scene.
    if best_result is None or not best_result.qa_passed:
        _emit("🔄 trying simplified fallback…")
        _log(f"[{scene.id}] Trying simplified fallback scene before human review…")
        fallback = await _try_simplified_fallback(llm, scene, spec, out_dir, _emit, _log)
        if fallback:
            return fallback

    if best_result:
        log.warning("manim_codegen.flagged_for_human", scene_id=scene.id)
        scene.set_manim_code(best_result.code)
        if best_result.clip_path:
            scene.set_clip(best_result.clip_path, qa_passed=False)
        else:
            scene.clip_path = None
            scene.clip_qa_passed = False
        return best_result

    scene.set_manim_code(code)
    scene.set_clip(None, qa_passed=False)
    return RenderResult(
        success=False,
        clip_path=None,
        qa_passed=False,
        code=code,
        attempts=max_repairs,
        flagged_for_human=True,
    )


async def run_manim_codegen(
    spec: VideoSpec,
    artifact_dir: str | None = None,
    max_repairs: int = 4,
    progress_cb: ProgressCb | None = None,
    log_cb: LogCb | None = None,
) -> VideoSpec:
    """Run Manim codegen for all manim/chart scenes in spec."""
    from app.models.video_spec import VisualType
    for scene in spec.scenes:
        if scene.visual_type not in (VisualType.manim, VisualType.chart):
            if progress_cb:
                progress_cb(scene.id, f"⏭️ skipped ({scene.visual_type.value})")
            continue
        if scene.clip_qa_passed:
            log.info("manim_codegen.scene_already_done", scene_id=scene.id)
            if progress_cb:
                progress_cb(scene.id, "✅ already done")
            continue
        if progress_cb:
            progress_cb(scene.id, "⏳ queued…")
        await render_scene(
            scene, spec,
            artifact_dir=artifact_dir,
            max_repairs=max_repairs,
            progress_cb=progress_cb,
            log_cb=log_cb,
        )
    return spec


# ── Repair helpers ─────────────────────────────────────────────────────────────

def _repair_temperature(attempt: int) -> float:
    return _REPAIR_TEMPS[min(attempt, len(_REPAIR_TEMPS) - 1)]


def _classify_error(traceback: str) -> str:
    for exc_type, hint in _ERROR_HINTS.items():
        if exc_type in traceback:
            return hint
    return ""


def _format_repair_history(history: list[dict]) -> str:
    if not history:
        return ""
    lines = ["\n=== REPAIR HISTORY (do NOT repeat these same mistakes) ==="]
    for i, entry in enumerate(history, 1):
        lines.append(f"Attempt {i} [{entry['error_type']}]: {entry['summary']}")
    lines.append("===\n")
    return "\n".join(lines)


# ── New pre-checks ─────────────────────────────────────────────────────────────

def _undefined_name_check(code: str) -> SandboxResult | None:
    """Statically catch NameErrors before paying for a sandbox render.

    Collects every name the code defines/imports plus the manim + numpy
    namespaces, then walks the AST for Name loads that resolve to nothing.
    Skips silently if manim is not importable in this process.
    """
    import builtins

    try:
        import manim as _manim
        import numpy as _np
    except ImportError:
        return None

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None  # _syntax_check handles this

    known: set[str] = set(dir(builtins)) | set(dir(_manim)) | {"np", "numpy", "manim", "self", "random"}

    class _Collector(ast.NodeVisitor):
        def visit_Import(self, node):
            for alias in node.names:
                known.add((alias.asname or alias.name).split(".")[0])
        def visit_ImportFrom(self, node):
            for alias in node.names:
                if alias.name == "*":
                    continue  # star imports already covered by manim namespace
                known.add(alias.asname or alias.name)
        def visit_FunctionDef(self, node):
            known.add(node.name)
            for a in node.args.args + node.args.kwonlyargs:
                known.add(a.arg)
            if node.args.vararg: known.add(node.args.vararg.arg)
            if node.args.kwarg: known.add(node.args.kwarg.arg)
            self.generic_visit(node)
        visit_AsyncFunctionDef = visit_FunctionDef
        def visit_Lambda(self, node):
            for a in node.args.args + node.args.kwonlyargs:
                known.add(a.arg)
            self.generic_visit(node)
        def visit_ClassDef(self, node):
            known.add(node.name)
            self.generic_visit(node)
        def visit_Name(self, node):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                known.add(node.id)
            self.generic_visit(node)
        def visit_comprehension(self, node):
            for n in ast.walk(node.target):
                if isinstance(n, ast.Name):
                    known.add(n.id)
            self.generic_visit(node)
        def visit_ExceptHandler(self, node):
            if node.name:
                known.add(node.name)
            self.generic_visit(node)
        def visit_With(self, node):
            for item in node.items:
                if item.optional_vars:
                    for n in ast.walk(item.optional_vars):
                        if isinstance(n, ast.Name):
                            known.add(n.id)
            self.generic_visit(node)
        def visit_For(self, node):
            for n in ast.walk(node.target):
                if isinstance(n, ast.Name):
                    known.add(n.id)
            self.generic_visit(node)

    _Collector().visit(tree)

    undefined: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in known and node.id not in undefined:
                undefined.append(node.id)

    if undefined:
        return SandboxResult(
            success=False,
            error_type="runtime_error",
            traceback=(
                f"NameError (caught statically): undefined name(s): {', '.join(undefined[:8])}\n"
                "Define these names, import them, or replace with valid Manim CE names.\n"
                "Note: only manim and numpy are importable in the sandbox."
            ),
        )
    return None


def _manimlib_pattern_check(code: str) -> SandboxResult | None:
    """Catch manimlib-only patterns that can't be auto-translated."""
    if "GraphScene" in code:
        return SandboxResult(
            success=False,
            error_type="runtime_error",
            traceback=(
                "GraphScene is old manimlib API and does not exist in Manim CE.\n"
                "Subclass Scene and build axes manually:\n"
                "    ax = Axes(x_range=[...], y_range=[...], axis_config={'color': P_AXIS})\n"
                "    curve = ax.plot(lambda x: ..., color=P_BLUE)"
            ),
        )
    import re
    if re.search(r"^\s*CONFIG\s*=\s*\{", code, re.MULTILINE):
        return SandboxResult(
            success=False,
            error_type="runtime_error",
            traceback=(
                "The CONFIG = {...} class dict is old manimlib API and is ignored by Manim CE.\n"
                "Set these options directly in construct() or pass them to constructors."
            ),
        )
    return None


def _mathtex_incomplete_check(code: str) -> SandboxResult | None:
    """Catch incomplete LaTeX commands like \\frac without two {}{} groups.

    These pass Python syntax but fail pdfLaTeX at runtime, wasting a sandbox slot.
    """
    import re
    tree = ast.parse(code)
    # Only commands that MUST be followed immediately by { } groups.
    # \sum/\int/\prod use subscripts/superscripts, not braces, so exclude them.
    incomplete_cmds = (r"\frac", r"\sqrt")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_tex_call(node):
            continue
        for arg in node.args:
            value = _literal_string(arg)
            if not value:
                continue
            for cmd in incomplete_cmds:
                # Find all occurrences of the command and check each is followed
                # by at least one {group}.
                for m in re.finditer(re.escape(cmd), value):
                    after = value[m.end():].lstrip()
                    if not after.startswith("{"):
                        return SandboxResult(
                            success=False,
                            error_type="runtime_error",
                            traceback=(
                                f"Incomplete LaTeX in MathTex: {cmd!r} must be followed by "
                                f"{{arg}} groups (e.g. \\frac{{a}}{{b}}).\n"
                                f"Bad fragment: {value!r}\n"
                                "Fix or replace with a simpler expression."
                            ),
                        )
    return None


def _missing_play_check(code: str) -> SandboxResult | None:
    """Fail early if construct() exists but no self.play() call — would render blank."""
    if "def construct" in code and "self.play(" not in code:
        return SandboxResult(
            success=False,
            error_type="runtime_error",
            traceback=(
                "Scene has construct() but no self.play() calls.\n"
                "The render would produce a blank frame with no animation.\n"
                "Add at least: self.play(Create(obj)) and self.play(Transform(a, b)) or .animate."
            ),
        )
    return None


def _beat_marker_check(code: str, scene: Scene) -> SandboxResult | None:
    """Warn if beat markers are missing when scene has beats."""
    if not scene.has_beats or not scene.beats:
        return None
    if "# ═══ BEAT" not in code:
        return SandboxResult(
            success=False,
            error_type="runtime_error",
            traceback=(
                f"Scene has {len(scene.beats)} beats but no '# ═══ BEAT' markers in code.\n"
                "Mark each beat section with:\n"
                "    # ═══ BEAT {beat_id}: {visual_action} ═══\n"
                "and add:  self.wait(1.0)  # beat boundary\n"
                "after each beat's animations."
            ),
        )
    return None


def _hardcoded_shift_check(code: str) -> SandboxResult | None:
    """Catch hardcoded non-integer magic-number shifts like .shift(RIGHT * 3.14159)."""
    import re
    pattern = re.compile(r'\.shift\(\s*(?:RIGHT|LEFT|UP|DOWN)\s*\*\s*\d+\.\d{3,}')
    m = pattern.search(code)
    if m:
        return SandboxResult(
            success=False,
            error_type="runtime_error",
            traceback=(
                f"Hardcoded magic-number shift detected: {m.group()!r}\n"
                "Use to_edge(), next_to(), move_to(), or arrange() for positioning.\n"
                "Frame is 14.22 × 8.0 units. Keep objects ≥0.5 units from any edge."
            ),
        )
    return None


# ── Simplified fallback ────────────────────────────────────────────────────────

async def _try_simplified_fallback(
    llm,
    scene: Scene,
    spec: VideoSpec,
    out_dir: str,
    emit_cb,
    log_cb,
) -> RenderResult | None:
    """Last-resort: generate a simpler scene before flagging for human review."""
    try:
        prompt = _GENERATE_PROMPT.format(
            visual_spec=scene.visual_spec,
            narration=scene.narration,
            language=spec.language,
            beats_section="",
        )
        resp = await llm.complete(
            [LLMMessage(role="user", content=prompt)],
            system=_FALLBACK_SYSTEM,
            max_tokens=_CODEGEN_MAX_TOKENS,
            temperature=0.4,
        )
        code = _postprocess_generated_code(_strip_code_fences(resp.content))

        syntax_err = _syntax_check(code) or _scene_subclass_check(code)
        if syntax_err:
            log_cb(f"[{scene.id}] Fallback scene failed syntax/subclass check — giving up")
            return None

        result = sandbox_exec(code, output_dir=out_dir)
        if not result.success:
            log_cb(f"[{scene.id}] Fallback scene failed sandbox — giving up")
            return None

        frames = sample_frames(
            result.clip_path, n=4,
            output_dir=os.path.join(out_dir, "frames_fallback"),
        )
        qa = await vision_qa(
            frames,
            intent=scene.visual_spec,
            narration=scene.narration,
            beats=_format_scene_beats(scene),
        )
        dest = _save_clip(result.clip_path, out_dir, scene.id)

        if qa.passed:
            scene.set_manim_code(code)
            scene.set_clip(dest, qa_passed=True)
            if scene.has_beats:
                durs = _extract_beat_durations_from_code(code, len(scene.beats))
                if durs:
                    scene.beat_render_durations = durs
            emit_cb("✅ simplified fallback passed QA")
            log_cb(f"[{scene.id}] ✅ Fallback QA passed. Clip saved to {dest}")
            log.info("manim_codegen.fallback_success", scene_id=scene.id)
            return RenderResult(success=True, clip_path=dest, qa_passed=True, code=code, attempts=-1)

        # Render succeeded but QA still failed — save as best-effort, still flag
        scene.set_manim_code(code)
        scene.set_clip(dest, qa_passed=False)
        emit_cb("⚠️ simplified fallback rendered but QA failed — flagged for review")
        log_cb(f"[{scene.id}] Fallback rendered; QA failed: {'; '.join(qa.issues)}")
        log.warning("manim_codegen.fallback_qa_fail", scene_id=scene.id, issues=qa.issues)
        return RenderResult(
            success=True, clip_path=dest, qa_passed=False, code=code,
            attempts=-1, flagged_for_human=True,
            error="\n".join(qa.issues),
        )

    except Exception as exc:
        log.warning("manim_codegen.fallback_error", scene_id=scene.id, error=str(exc))
        return None


# ── LLM call helpers ───────────────────────────────────────────────────────────

async def _generate_code(llm, scene: Scene, spec: VideoSpec) -> str:
    # Template RAG: retrieve matching templates to give LLM a head start
    from app.agents.template_rag import format_template_context, retrieve_templates_fast

    beat_actions = [b.visual_action for b in sorted(scene.beats, key=lambda b: b.order)] if scene.has_beats else []
    template_matches = retrieve_templates_fast(scene.visual_spec, beat_actions=beat_actions)
    template_context = format_template_context(template_matches)

    beats_section = ""
    if scene.has_beats:
        from app.models.video_spec import words_per_second
        wps = words_per_second(spec.language)
        beats_list = "\n".join(
            f"  {b.order}. [{b.id}] {b.visual_action}\n"
            f"     Trigger phrase: \"{b.trigger_phrase}\"\n"
            f"     Narration: \"{b.narration_segment}\"\n"
            f"     Target animation time: ~{max(len(b.narration_segment.split()) / wps, 2.0):.0f}s "
            f"(spread run_time= and self.wait() to fill it)\n"
            f"     Must show: {_format_list_for_prompt(b.must_show)}\n"
            f"     On-screen label: {b.on_screen_label or '(none)'}\n"
            f"     Forbidden visuals: {_format_list_for_prompt(b.forbidden_visuals)}"
            for b in sorted(scene.beats, key=lambda b: b.order)
        )
        beats_list_escaped = beats_list.replace("{", "{{").replace("}", "}}")
        beats_section = _GENERATE_BEATS_SECTION.format(
            n_beats=len(scene.beats),
            beats_list=beats_list_escaped,
            beat_id="{beat_id}",
            visual_action="{visual_action}",
        )

    # Append template context to beats section
    if template_context:
        beats_section = beats_section + template_context

    prompt = _GENERATE_PROMPT.format(
        visual_spec=scene.visual_spec,
        narration=scene.narration,
        language=spec.language,
        beats_section=beats_section,
    )
    resp = await llm.complete(
        [LLMMessage(role="user", content=prompt)],
        system=_GENERATE_SYSTEM,
        max_tokens=_CODEGEN_MAX_TOKENS,
        temperature=0.3,
    )
    code = _strip_code_fences(resp.content)
    return _postprocess_generated_code(code)


async def _repair_runtime(
    llm,
    code: str,
    result: SandboxResult,
    history: list[dict],
    attempt: int,
    force_diff: bool = False,
) -> str:
    hint = _classify_error(result.traceback or result.stderr or "")
    history_section = _format_repair_history(history)
    force_note = (
        "\n\nWARNING: Your previous response was IDENTICAL to the input code. "
        "You MUST make a substantive change. If you don't know how to fix this error, "
        "try a completely different approach to generating the same visual."
    ) if force_diff else ""

    prompt = _REPAIR_RUNTIME_PROMPT.format(
        history=history_section,
        code=code,
        traceback=result.traceback or result.stderr,
        hint=f"\nTriage hint: {hint}" if hint else "",
        force_note=force_note,
    )
    temp = _repair_temperature(attempt)
    resp = await llm.complete(
        [LLMMessage(role="user", content=prompt)],
        system=_GENERATE_SYSTEM,
        max_tokens=_CODEGEN_MAX_TOKENS,
        temperature=temp,
    )
    code = _strip_code_fences(resp.content)
    return _postprocess_generated_code(code)


def _extract_beat_durations_from_code(code: str, n_beats: int) -> list[float]:
    """Parse Manim code to estimate per-beat animation durations via static analysis.

    Splits code at '# ═══ BEAT' markers, then sums self.play(run_time=X) and
    self.wait(X) values within each section. Falls back to 1.0s per call when
    run_time is not specified (Manim default).

    Returns [] if section count doesn't match n_beats (can't reliably assign).
    """
    import re

    sections = re.split(r'#\s*═+\s*BEAT\s+\S+', code)
    beat_sections = sections[1:]  # sections[0] is preamble before first beat
    if len(beat_sections) != n_beats:
        return []

    durations: list[float] = []
    for section in beat_sections:
        total = 0.0
        for m in re.finditer(r'self\.wait\(([^)]*)\)', section):
            arg = m.group(1).strip()
            try:
                total += float(arg) if arg else 1.0
            except ValueError:
                total += 1.0
        for m in re.finditer(r'self\.play\(', section):
            rt = re.search(r'run_time\s*=\s*([0-9.]+)', section[m.start():m.start() + 400])
            total += float(rt.group(1)) if rt else 1.0
        durations.append(max(total, 0.5))
    return durations


def _short_error(result: SandboxResult, max_chars: int = 1200) -> str:
    text = result.traceback or result.stderr or result.stdout or "No sandbox error output."
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return "No sandbox error output."
    tail = "\n".join(lines[-12:])
    if len(tail) > max_chars:
        return tail[-max_chars:]
    return tail


def _is_qa_infrastructure_error(issues: list[str]) -> bool:
    parse_markers = (
        "Visual QA parse error",
        "JSON parse error",
        "Could not parse QA response",
    )
    return any(any(marker in issue for marker in parse_markers) for issue in issues)


async def _repair_visual(
    llm,
    code: str,
    qa: QAResult,
    scene: Scene,
    history: list[dict],
    attempt: int,
    force_diff: bool = False,
) -> str:
    # Phase-based repair: fix correctness first (even attempts), style second (odd).
    if qa.correctness_issues and (attempt % 2 == 0 or not qa.style_issues):
        priority_issues = qa.correctness_issues
        phase_label = "CORRECTNESS ISSUES"
        phase_note = (
            "Focus ONLY on correctness issues listed above. "
            "Do not rewrite style or pacing unless it directly causes a correctness problem."
        )
    else:
        priority_issues = qa.style_issues or qa.issues
        phase_label = "STYLE / ANIMATION QUALITY ISSUES"
        phase_note = (
            "Correctness is acceptable. Focus ONLY on the style and animation quality issues listed above."
        )

    history_section = _format_repair_history(history)
    force_note = (
        "\n\nWARNING: Your previous response was IDENTICAL to the input code. "
        "You MUST restructure at least one section of the animation to address the issues above."
    ) if force_diff else ""

    beats = _format_scene_beats(scene)
    prompt = _REPAIR_VISUAL_PROMPT.format(
        visual_spec=scene.visual_spec,
        narration=scene.narration,
        beats=beats,
        history=history_section,
        code=code,
        phase_label=phase_label,
        priority_issues="\n".join(f"- {i}" for i in priority_issues),
        all_issues="\n".join(f"- {i}" for i in qa.issues),
        phase_note=phase_note,
        force_note=force_note,
    )
    temp = _repair_temperature(attempt)
    resp = await llm.complete(
        [LLMMessage(role="user", content=prompt)],
        system=_GENERATE_SYSTEM,
        max_tokens=_CODEGEN_MAX_TOKENS,
        temperature=temp,
    )
    return _postprocess_generated_code(_strip_code_fences(resp.content))


def _format_scene_beats(scene: Scene) -> str:
    if not scene.beats:
        return "No explicit beats. Create 3-5 staged moments from the visual intent."
    return "\n".join(
        f"{beat.order}. {beat.visual_action}\n"
        f"   Trigger: {beat.trigger_phrase}\n"
        f"   Narration: {beat.narration_segment}\n"
        f"   Must show: {_format_list_for_prompt(beat.must_show)}\n"
        f"   On-screen label: {beat.on_screen_label or '(none)'}\n"
        f"   Forbidden visuals: {_format_list_for_prompt(beat.forbidden_visuals)}"
        for beat in sorted(scene.beats, key=lambda b: b.order)
    )


def _format_list_for_prompt(items: list[str]) -> str:
    clean = [str(item).strip() for item in items if str(item).strip()]
    return "; ".join(clean) if clean else "(none)"


# ── Static pre-checks ──────────────────────────────────────────────────────────

def _syntax_check(code: str) -> SandboxResult | None:
    """Return a runtime-style failure if generated code is not valid Python."""
    try:
        compile(code, "generated_manim_scene.py", "exec")
    except SyntaxError as exc:
        return SandboxResult(
            success=False,
            error_type="runtime_error",
            traceback="".join(traceback_module.format_exception_only(type(exc), exc)).strip(),
        )
    return None


_PALETTE_HEADER = """\
BACKGROUND_COLOR = "#1C1C2E"
P_BLUE   = "#58C4DD"
P_GREEN  = "#58A162"
P_YELLOW = "#FFFF00"
P_GOLD   = "#C49A04"
P_RED    = "#FC6255"
P_TEAL   = "#49A88F"
P_WHITE  = "#FFFFFF"
P_GREY   = "#BDBDBD"
P_AXIS   = "#1C758A"
P_DIM    = "#55534E"
"""


def _postprocess_generated_code(code: str) -> str:
    code = _fix_manimlib_api(code)
    code = _inject_palette_if_missing(code)
    code = _ensure_scene_subclass(code)
    code = _fix_background_color(code)
    code = _fix_unsupported_num_points(code)
    code = _fix_opacity_kwarg(code)
    code = _fix_missing_random_import(code)
    code = _fix_camera_scene_usage(code)
    code = _fix_interpolate_color_args(code)
    code = _fix_zero_waits(code)
    return _fix_zero_animations(code)


_MANIMLIB_REPLACEMENTS = [
    # (pattern, replacement) — translate old manimlib API to Manim CE.
    # These leak constantly from LLM training data and are the single biggest
    # source of AttributeError/NameError repair cycles.
    (r"\bShowCreation\b", "Create"),
    (r"\bShowCreationThenFadeOut\b", "Create"),
    (r"\bTextMobject\b", "Text"),
    (r"\bTexMobject\b", "MathTex"),
    (r"\bOldTex\b", "MathTex"),
    (r"\.get_graph\(", ".plot("),
    (r"\bFadeInFromDown\b", "FadeIn"),
    (r"\bFadeOutAndShiftDown\b", "FadeOut"),
]


def _fix_manimlib_api(code: str) -> str:
    """Translate common old-manimlib API leakage into Manim CE equivalents."""
    import re
    fixed = code
    for pattern, replacement in _MANIMLIB_REPLACEMENTS:
        fixed = re.sub(pattern, replacement, fixed)
    if fixed != code:
        log.warning("manim_codegen.auto_fix_manimlib_api")
    return fixed


def _fix_zero_waits(code: str) -> str:
    """Replace self.wait(0) — an explicit anti-pattern — with a short real pause."""
    import re
    fixed = re.sub(r"self\.wait\(\s*0(?:\.0+)?\s*\)", "self.wait(0.5)", code)
    if fixed != code:
        log.warning("manim_codegen.auto_fix_zero_waits")
    return fixed


def _inject_palette_if_missing(code: str) -> str:
    """Add palette constants after imports if any P_* or BACKGROUND_COLOR is used but not defined."""
    import re
    used = set(re.findall(r'\b(P_[A-Z]+|BACKGROUND_COLOR)\b', code))
    if not used:
        return code
    defined = set(re.findall(r'^(P_[A-Z]+|BACKGROUND_COLOR)\s*=', code, re.MULTILINE))
    if not (used - defined):
        return code
    lines = code.splitlines(keepends=True)
    insert_at = 0
    for i, line in enumerate(lines):
        if line.strip().startswith(("import ", "from ")):
            insert_at = i + 1
    lines.insert(insert_at, "\n" + _PALETTE_HEADER + "\n")
    return "".join(lines)


def _fix_background_color(code: str) -> str:
    """Force generated Manim scenes to use the app palette background."""
    import re

    fixed = code
    fixed = re.sub(
        r"(self\.camera\.background_color\s*=\s*)(?:BLACK|\"#000000\"|'#000000'|\"#000\"|'#000')",
        r"\1BACKGROUND_COLOR",
        fixed,
    )
    fixed = re.sub(
        r"(config\.background_color\s*=\s*)(?:BLACK|\"#000000\"|'#000000'|\"#000\"|'#000')",
        r"\1BACKGROUND_COLOR",
        fixed,
    )

    if "self.camera.background_color" not in fixed:
        fixed = re.sub(
            r"(\n\s*def construct\(self\):\n)",
            r"\1        self.camera.background_color = BACKGROUND_COLOR\n",
            fixed,
            count=1,
        )

    if fixed != code:
        log.warning("manim_codegen.auto_fix_background_color")
    return fixed


def _fix_unsupported_num_points(code: str) -> str:
    """Remove generated num_points= kwargs that Manim CE mobjects don't accept."""
    import re

    fixed = re.sub(r",\s*num_points\s*=\s*[^,)]+", "", code)
    fixed = re.sub(r"num_points\s*=\s*[^,)]+\s*,\s*", "", fixed)
    if fixed != code:
        log.warning("manim_codegen.auto_fix_unsupported_num_points")
    return fixed


def _fix_missing_random_import(code: str) -> str:
    """Inject 'import random' if code uses random.* but doesn't import it."""
    import re
    if "random." not in code:
        return code
    if re.search(r"^import random\b", code, re.MULTILINE):
        return code
    # Insert after the last stdlib/manim import line
    lines = code.splitlines(keepends=True)
    insert_at = 0
    for i, line in enumerate(lines):
        if line.strip().startswith(("import ", "from ")):
            insert_at = i + 1
    lines.insert(insert_at, "import random\n")
    fixed = "".join(lines)
    log.warning("manim_codegen.auto_fix_missing_random_import")
    return fixed


def _fix_opacity_kwarg(code: str) -> str:
    """Replace bare opacity= kwarg with fill_opacity= in Mobject constructor calls.

    VMobject.__init__ does not accept opacity= — use fill_opacity= or stroke_opacity=.
    This only replaces bare opacity= (not already prefixed with fill_ or stroke_).
    """
    import re
    fixed = re.sub(r'(?<![a-z_])opacity=', 'fill_opacity=', code)
    if fixed != code:
        log.warning("manim_codegen.auto_fix_opacity_kwarg")
    return fixed


def _fix_camera_scene_usage(code: str) -> str:
    """Use MovingCameraScene for generated camera-frame zooms/pans."""
    import re

    fixed = code
    fixed = re.sub(
        r"self\.camera\.animate\.set_frame_width\(([^)]+)\)\.move_to\(ax\.c2p\(([^)]*)\)\)",
        r"self.camera.frame.animate.move_to(ax.c2p(\2)).set(width=\1)",
        fixed,
    )
    fixed = re.sub(
        r"self\.camera\.animate\.set_frame_width\(([^)]+)\)\.move_to\(([^()]+)\)",
        r"self.camera.frame.animate.move_to(\2).set(width=\1)",
        fixed,
    )

    if "self.camera.frame" in fixed:
        fixed = re.sub(r"class(\s+\w+\s*)\(\s*Scene\s*\):", r"class\1(MovingCameraScene):", fixed, count=1)

    if fixed != code:
        log.warning("manim_codegen.auto_fix_camera_scene_usage")
    return fixed


def _fix_interpolate_color_args(code: str) -> str:
    """Wrap string palette constants passed to interpolate_color() as ManimColor(...)."""
    import re

    color_arg = r"(?:P_[A-Z]+|BACKGROUND_COLOR|['\"]#[0-9A-Fa-f]{6}['\"])"
    pattern = re.compile(rf"interpolate_color\(\s*({color_arg})\s*,\s*({color_arg})\s*,")

    def _wrap(arg: str) -> str:
        return arg if arg.startswith("ManimColor(") else f"ManimColor({arg})"

    fixed = pattern.sub(lambda m: f"interpolate_color({_wrap(m.group(1))}, {_wrap(m.group(2))},", code)
    if fixed != code:
        log.warning("manim_codegen.auto_fix_interpolate_color_args")
    return fixed


def _fix_zero_animations(code: str) -> str:
    """Replace bare self.add() with self.play(FadeIn()) when no self.play() exists."""
    import re
    if "self.play(" in code:
        return code

    def _replace(m: re.Match) -> str:
        args = m.group(1).strip()
        line_start = code.rfind('\n', 0, m.start()) + 1
        indent = ' ' * (m.start() - line_start)
        return f"self.play(FadeIn({args}))\n{indent}self.wait(0.5)"

    fixed = re.sub(r"self\.add\(([^)]+)\)", _replace, code)
    if fixed != code:
        log.warning("manim_codegen.auto_fix_zero_animations", reason="no self.play() found, replaced self.add()")
    return fixed


def _has_scene_subclass(code: str) -> bool:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                name = (base.id if isinstance(base, ast.Name)
                        else base.attr if isinstance(base, ast.Attribute) else "")
                if name in {"Scene", "MovingCameraScene", "ThreeDScene"}:
                    return True
    return False


def _ensure_scene_subclass(code: str) -> str:
    """Wrap code in a Scene class if the LLM forgot to include one."""
    if _has_scene_subclass(code):
        return code

    import_lines, body_lines = [], []
    for line in code.splitlines():
        s = line.strip()
        if s.startswith(("import ", "from ")) or (not s and not body_lines):
            import_lines.append(line)
        else:
            body_lines.append(line)

    imports = "\n".join(import_lines) if import_lines else "from manim import *\nimport numpy as np"
    if "BACKGROUND_COLOR" not in imports:
        imports += '\n\nBACKGROUND_COLOR = "#1C1C2E"'

    body = "\n".join(body_lines)
    has_construct = "def construct" in body

    base_class = "MovingCameraScene" if "self.camera.frame" in body else "Scene"

    if has_construct:
        indented = "\n".join("    " + l for l in body.splitlines())
        log.warning("manim_codegen.autowrap_construct", reason="no Scene subclass, had construct()")
        return f"{imports}\n\nclass GeneratedScene({base_class}):\n{indented}\n"
    else:
        indented = "\n".join("        " + l for l in body.splitlines())
        log.warning("manim_codegen.autowrap_body", reason="no Scene subclass, no construct()")
        return (
            f"{imports}\n\nclass GeneratedScene({base_class}):\n"
            f"    def construct(self):\n"
            f"        self.camera.background_color = BACKGROUND_COLOR\n"
            f"{indented}\n"
        )


def _scene_subclass_check(code: str) -> SandboxResult | None:
    """Return failure if no class inheriting from Scene is found."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None  # _syntax_check handles this
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                name = base.id if isinstance(base, ast.Name) else (base.attr if isinstance(base, ast.Attribute) else "")
                if name in {"Scene", "MovingCameraScene", "ThreeDScene"}:
                    return None
    return SandboxResult(
        success=False,
        error_type="runtime_error",
        traceback=(
            "No Scene subclass found in generated code.\n"
            "You MUST define exactly one class inheriting from Scene:\n\n"
            "    class YourSceneName(Scene):\n"
            "        def construct(self):\n"
            "            self.camera.background_color = BACKGROUND_COLOR\n"
            "            ...\n\n"
            "Output ONLY the complete Python file. Do not wrap in markdown."
        ),
    )


def _latex_source_check(code: str) -> SandboxResult | None:
    """Catch invalid or paper-only Tex/MathTex before pdfLaTeX/QA fails later."""
    tree = ast.parse(code)
    forbidden = (
        "\\cite",
        "\\bibitem",
        "\\bibliography",
        "\\begin{thebibliography}",
        "\\end{thebibliography}",
    )
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_tex_call(node):
            continue
        for arg in node.args:
            value = _literal_string(arg)
            if not value:
                continue
            lowered = value.lower()
            if any(token.lower() in lowered for token in forbidden):
                return SandboxResult(
                    success=False,
                    error_type="runtime_error",
                    traceback=(
                        "Invalid Manim LaTeX source: Tex/MathTex contains bibliography "
                        f"or citation markup: {value!r}\n"
                        "Rendered explainer scenes must not include paper citations, "
                        "bibliography commands, or reference fragments. Replace them "
                        "with a short visual label or remove them."
                    ),
                )
            if not value.isascii():
                return SandboxResult(
                    success=False,
                    error_type="runtime_error",
                    traceback=(
                        "Invalid Manim LaTeX source: Tex/MathTex received non-ASCII text.\n"
                        f"Bad fragment: {value!r}\n"
                        "Tex/MathTex must contain ASCII LaTeX only. Move natural-language "
                        "or Vietnamese text into Text(...), and keep formulas in separate "
                        "MathTex(...) objects."
                    ),
                )
    return None


def _is_tex_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in {"Tex", "MathTex"}
    if isinstance(func, ast.Attribute):
        return func.attr in {"Tex", "MathTex"}
    return False


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = [part.value for part in node.values if isinstance(part, ast.Constant) and isinstance(part.value, str)]
        return "".join(parts)
    return None


def _check_cache(scene: Scene, out_dir: str) -> str | None:
    if not scene.manim_code_hash:
        return None
    candidate = os.path.join(out_dir, f"{scene.id}.mp4")
    if Path(candidate).exists():
        import hashlib
        code = scene.manim_code or ""
        h = hashlib.sha256(code.encode()).hexdigest()
        if h == scene.manim_code_hash:
            return candidate
    return None


def _save_clip(src: str, out_dir: str, scene_id: str) -> str:
    dest = os.path.join(out_dir, f"{scene_id}.mp4")
    if Path(src).resolve() == Path(dest).resolve():
        return dest
    shutil.copy2(src, dest)
    return dest


def _strip_code_fences(text: str) -> str:
    import re
    # Belt-and-suspenders: strip DeepSeek/R1 thinking blocks even if the provider already did it.
    stripped = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # Prefer fenced blocks — handles ```python, ```py, ``` variants.
    fenced = re.search(r"```(?:python|py)?\s*\r?\n(.*?)\r?\n```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()

    # Look for the real code start: prefer `from manim import` (most specific), then `class *Scene`.
    # Use line-anchored search so we don't match prose mentions of these words.
    line_anchored_starts = []
    for pattern in (r"^from manim import", r"^import manim", r"^class \w+\s*\("):
        m = re.search(pattern, stripped, re.MULTILINE)
        if m:
            line_anchored_starts.append(m.start())
    if line_anchored_starts:
        return stripped[min(line_anchored_starts):].strip()

    # Fallback: strip any remaining fence markers.
    stripped = re.sub(r"^```(?:python|py)?\n?", "", stripped, flags=re.MULTILINE | re.IGNORECASE)
    stripped = re.sub(r"\n?```$", "", stripped, flags=re.MULTILINE)
    return stripped.strip()
