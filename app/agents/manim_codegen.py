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
You are an expert Manim Community Edition (CE) developer generating animation scenes for
math/physics explainer videos. Write clean, correct Manim CE code only.

Rules:
- Use Manim CE API only (not manimlib). Import: `from manim import *`
- One Scene subclass per file named descriptively
- No network imports, no file I/O outside /workspace
- Keep total animation ≤ 80 seconds (use run_time= to control)
- Avoid overlapping objects: use VGroup(...).arrange(DOWN, buff=0.4)
- MathTex for formulas; check width < 12 units, scale down if needed
- Use Create for shapes, Write for text/formulas, FadeIn for general Mobjects
- Default frame: 1920×1080, ORIGIN at center
"""

_GENERATE_PROMPT = """\
Create a Manim CE scene for the following animation:

Visual intent: {visual_spec}
Narration context: {narration}
Language: {language}

Output ONLY the Python source code, no explanations, no markdown fences.
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
    prompt = _GENERATE_PROMPT.format(
        visual_spec=scene.visual_spec,
        narration=scene.narration,
        language=spec.language,
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
