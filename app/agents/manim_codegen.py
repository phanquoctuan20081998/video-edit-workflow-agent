"""Stage 3 — Manim codegen agent with self-repair loop.

generate → sandbox_exec → [repair loop] → visual QA

Two distinct repair paths:
  1. runtime_error  → pass traceback, fix syntax/API errors
  2. visual QA fail → pass frame screenshots + issues, fix visual layout

Cache: checks manim_code_hash before any exec.
Cap: raises RepairCapExceeded after max_repairs attempts.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import structlog

from app.agents.visual_qa import vision_qa
from app.config import get_settings
from app.models.video_spec import Scene, VideoSpec
from app.providers.base import LLMMessage
from app.providers.factory import get_llm_provider
from app.sandbox.frame_sampler import sample_frames
from app.sandbox.runner import SandboxResult, sandbox_exec

log = structlog.get_logger()

_GENERATE_SYSTEM = """\
You are an expert Manim Community Edition (CE) developer generating math/physics explainer
animations in the style of 3Blue1Brown. Your output must be visually clean, mathematically
purposeful, and never "AI slop" (random colors, wall-of-text reveals, static objects).

═══ API RULES ═══
- Manim CE only (not manimlib). Always start with the STYLE_HEADER block below.
- One Scene subclass per file, class name matches the concept.
- No network imports, no file I/O outside /workspace.
- Total animation ≤ 80 seconds. Use run_time= to control pacing.

═══ STYLE HEADER (copy verbatim at top of every file) ═══
from manim import *
import numpy as np

BACKGROUND_COLOR = "#1C1C2E"
P_BLUE   = "#58C4DD"   # primary objects
P_GREEN  = "#83C167"   # secondary objects
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
Assign ONE meaning per color, keep it for the entire scene:
  P_BLUE   → primary mathematical object (vector, curve, key shape)
  P_GREEN  → secondary / supporting object
  P_YELLOW → final result, answer, or peak emphasis (use sparingly)
  P_RED    → negation, cancellation, what's being removed
  P_AXIS   → NumberPlane, Axes, grid (never dominant)
  P_DIM    → DashedLine, construction aids
  P_WHITE  → ALL text and MathTex
NEVER assign colors arbitrarily. Viewer infers: same color = same concept.

═══ TYPOGRAPHY ═══
- MathTex for ALL math. Never: Text("f(x) = x²") — always: MathTex(r"f(x) = x^2")
- After creating MathTex, check width: if tex.width > 10: tex.scale(10 / tex.width)
- Title: Text("title", font_size=40, color=P_WHITE).to_edge(UP, buff=0.5)
- Labels: scale(0.65) relative to main objects, next_to(obj, direction, buff=0.25)

═══ LAYOUT ═══
- No overlaps. Stack with: VGroup(a, b, c).arrange(DOWN, buff=0.75)
- Position with to_edge(), next_to(), move_to() — NEVER hardcode .shift(3.14)
- Margin: nothing within 0.5 units of frame edge (frame = 14.22 × 8.0 units)
- Max 6–8 objects visible simultaneously. More → split or FadeOut old ones.

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

═══ ANTI-PATTERNS — NEVER GENERATE ═══
❌ self.add(obj1, obj2, obj3, obj4) — all at once, no animation
❌ FadeIn(equation) — use Write(equation)
❌ Objects that appear and never interact, move, or change color
❌ Random color per object (BLUE, GREEN, RED, ORANGE all in one scene with no meaning)
❌ Missing waits between reveals
❌ MathTex wider than 10 units (always check .width)
❌ Text() for math expressions
❌ Hardcoded shifts: .shift(RIGHT * 3.14159)
❌ Pure black background: background_color = BLACK
❌ Showing the entire derivation in one screen with 8+ equations simultaneously
"""

_GENERATE_PROMPT = """\
Create a Manim CE scene for the following animation:

Visual intent: {visual_spec}
Narration context: {narration}
Language: {language}
{beats_section}
Output ONLY the Python source code, no explanations, no markdown fences.
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

CRITICAL: The animation must be continuous. Objects created in beat 1 should still be
visible/transformable in beat 3. Only FadeOut objects when the narration explicitly
moves past them. The self.wait() between beats is where the compositor will cut if using
split-render strategy, or where timing will be adjusted.
"""

_REPAIR_RUNTIME_PROMPT = """\
The following Manim CE code produced a runtime error. Fix it.

=== CURRENT CODE ===
{code}

=== ERROR TRACEBACK ===
{traceback}

Output ONLY the fixed Python source code, no explanations, no markdown fences.
"""

_REPAIR_VISUAL_PROMPT = """\
The following Manim CE code rendered successfully but has visual problems. Fix the visual issues.

=== CURRENT CODE ===
{code}

=== VISUAL PROBLEMS REPORTED ===
{issues}

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


async def render_scene(
    scene: Scene,
    spec: VideoSpec,
    artifact_dir: str | None = None,
    max_repairs: int = 4,
    n_variants: int = 1,
) -> RenderResult:
    """Generate + exec + repair + QA a single scene. Updates scene in-place on success."""
    cfg = get_settings()
    base_dir = artifact_dir or cfg.artifact_dir
    out_dir = os.path.join(base_dir, spec.project_id, "scenes", scene.id)
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    # Cache check
    cached = _check_cache(scene, out_dir)
    if cached:
        log.info("manim_codegen.cache_hit", scene_id=scene.id, hash=scene.manim_code_hash)
        scene.set_clip(cached, qa_passed=True)
        return RenderResult(success=True, clip_path=cached, qa_passed=True, code=scene.manim_code, attempts=0)

    llm = get_llm_provider()
    best_result: RenderResult | None = None

    for variant in range(n_variants):
        log.info("manim_codegen.generate", scene_id=scene.id, variant=variant)
        code = await _generate_code(llm, scene, spec)
        total_attempts = 0

        for attempt in range(max_repairs + 1):
            total_attempts += 1
            log.info("manim_codegen.exec", scene_id=scene.id, attempt=attempt)
            scene.set_manim_code(code)
            sandbox_result = sandbox_exec(code, output_dir=out_dir)

            if not sandbox_result.success:
                if attempt == max_repairs:
                    log.warning("manim_codegen.repair_cap_runtime", scene_id=scene.id)
                    break
                log.info("manim_codegen.repair_runtime", scene_id=scene.id, error_type=sandbox_result.error_type)
                code = await _repair_runtime(llm, code, sandbox_result)
                continue

            # Sandbox succeeded — run visual QA
            frames = sample_frames(sandbox_result.clip_path, n=4, output_dir=os.path.join(out_dir, "frames"))
            qa = await vision_qa(frames, intent=scene.visual_spec, narration=scene.narration)

            if qa.passed:
                dest = _save_clip(sandbox_result.clip_path, out_dir, scene.id)
                scene.set_manim_code(code)
                scene.set_clip(dest, qa_passed=True)
                log.info("manim_codegen.success", scene_id=scene.id, attempts=total_attempts)
                return RenderResult(success=True, clip_path=dest, qa_passed=True, code=code, attempts=total_attempts)

            if attempt == max_repairs:
                # Best-effort: save even if QA failed, flag for human
                dest = _save_clip(sandbox_result.clip_path, out_dir, scene.id)
                best_result = RenderResult(
                    success=True, clip_path=dest, qa_passed=False, code=code,
                    attempts=total_attempts, flagged_for_human=True,
                )
                break

            log.info("manim_codegen.repair_visual", scene_id=scene.id, issues=qa.issues)
            code = await _repair_visual(llm, code, qa.issues)

    if best_result:
        log.warning("manim_codegen.flagged_for_human", scene_id=scene.id)
        scene.set_manim_code(best_result.code)
        scene.set_clip(best_result.clip_path, qa_passed=False)
        return best_result

    scene.set_clip(None, qa_passed=False)
    return RenderResult(success=False, clip_path=None, qa_passed=False, code=code, attempts=max_repairs, flagged_for_human=True)


async def run_manim_codegen(spec: VideoSpec, artifact_dir: str | None = None, max_repairs: int = 4) -> VideoSpec:
    """Run Manim codegen for all manim/chart scenes in spec."""
    from app.models.video_spec import VisualType
    for scene in spec.scenes:
        if scene.visual_type not in (VisualType.manim, VisualType.chart):
            continue
        if scene.clip_qa_passed:
            log.info("manim_codegen.scene_already_done", scene_id=scene.id)
            continue
        await render_scene(scene, spec, artifact_dir=artifact_dir, max_repairs=max_repairs)
    return spec


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _generate_code(llm, scene: Scene, spec: VideoSpec) -> str:
    beats_section = ""
    if scene.has_beats:
        beats_list = "\n".join(
            f"  {b.order}. [{b.id}] {b.visual_action}\n"
            f"     Narration: \"{b.narration_segment[:80]}...\""
            for b in sorted(scene.beats, key=lambda b: b.order)
        )
        # Escape any braces in beats_list before inserting into format string
        beats_list_escaped = beats_list.replace("{", "{{").replace("}", "}}")
        beats_section = _GENERATE_BEATS_SECTION.format(
            n_beats=len(scene.beats),
            beats_list=beats_list_escaped,
            beat_id="{beat_id}",
            visual_action="{visual_action}",
        )

    prompt = _GENERATE_PROMPT.format(
        visual_spec=scene.visual_spec,
        narration=scene.narration,
        language=spec.language,
        beats_section=beats_section,
    )
    resp = await llm.complete(
        [LLMMessage(role="user", content=prompt)],
        system=_GENERATE_SYSTEM,
        max_tokens=2048,
        temperature=0.3,
    )
    return _strip_code_fences(resp.content)


async def _repair_runtime(llm, code: str, result: SandboxResult) -> str:
    prompt = _REPAIR_RUNTIME_PROMPT.format(code=code, traceback=result.traceback or result.stderr)
    resp = await llm.complete(
        [LLMMessage(role="user", content=prompt)],
        system=_GENERATE_SYSTEM,
        max_tokens=2048,
        temperature=0.2,
    )
    return _strip_code_fences(resp.content)


async def _repair_visual(llm, code: str, issues: list[str]) -> str:
    prompt = _REPAIR_VISUAL_PROMPT.format(code=code, issues="\n".join(f"- {i}" for i in issues))
    resp = await llm.complete(
        [LLMMessage(role="user", content=prompt)],
        system=_GENERATE_SYSTEM,
        max_tokens=2048,
        temperature=0.2,
    )
    return _strip_code_fences(resp.content)


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
    shutil.copy2(src, dest)
    return dest


def _strip_code_fences(text: str) -> str:
    import re
    text = re.sub(r"^```(?:python)?\n?", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\n?```$", "", text.strip(), flags=re.MULTILINE)
    return text.strip()
