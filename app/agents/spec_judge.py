"""VideoSpec judge + reflect loop (inspired by VideoAgent's two-step self-evaluation).

Validates a VideoSpec BEFORE passing it to Manim codegen (expensive).
If validation fails, produces structured reflection for the script agent to regenerate.

Two-step process:
  1. Judge — check structural integrity + Manim feasibility
  2. Reflect — if judgment fails, analyze WHY and produce actionable feedback

This catches bad specs early, avoiding wasted Manim renders on specs where:
  - trigger_phrases aren't substrings of narration
  - visual_spec is not feasible for Manim (photos, real-world footage, etc.)
  - beats have gaps/overlaps in narration coverage
  - scene ordering is illogical
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import structlog

from app.models.video_spec import Scene, VideoSpec, VisualType
from app.providers.base import LLMMessage
from app.providers.factory import get_llm_provider

log = structlog.get_logger()

MAX_JUDGE_REFLECTIONS = 3


@dataclass
class JudgeIssue:
    severity: str  # "error" | "warning"
    scene_id: str | None
    beat_id: str | None
    category: str  # "trigger_phrase" | "feasibility" | "coverage" | "ordering" | "visual_spec"
    message: str


@dataclass
class JudgeResult:
    passed: bool
    issues: list[JudgeIssue] = field(default_factory=list)
    reflection: str | None = None  # Actionable feedback for script agent

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")


# ── Structural validators (deterministic, no LLM) ─────────────────────────────

def _check_trigger_phrases(spec: VideoSpec) -> list[JudgeIssue]:
    """Every beat.trigger_phrase must be an exact substring of its scene's narration."""
    issues = []
    for scene in spec.scenes:
        for beat in scene.beats:
            if beat.trigger_phrase and beat.trigger_phrase not in scene.narration:
                issues.append(JudgeIssue(
                    severity="error",
                    scene_id=scene.id,
                    beat_id=beat.id,
                    category="trigger_phrase",
                    message=f"trigger_phrase '{beat.trigger_phrase[:60]}…' not found in scene narration",
                ))
    return issues


def _check_narration_coverage(spec: VideoSpec) -> list[JudgeIssue]:
    """Beat narration_segments should cover full narration without large gaps."""
    issues = []
    for scene in spec.scenes:
        if not scene.beats:
            continue
        segments_concat = "".join(b.narration_segment for b in scene.beats)
        # Allow some whitespace tolerance
        narration_stripped = scene.narration.replace(" ", "").replace("\n", "")
        segments_stripped = segments_concat.replace(" ", "").replace("\n", "")
        # Check coverage ratio
        if narration_stripped and segments_stripped:
            ratio = len(segments_stripped) / len(narration_stripped)
            if ratio < 0.7:
                issues.append(JudgeIssue(
                    severity="warning",
                    scene_id=scene.id,
                    beat_id=None,
                    category="coverage",
                    message=f"Beat segments cover only {ratio:.0%} of scene narration (expected ≥70%)",
                ))
    return issues


def _check_beat_ordering(spec: VideoSpec) -> list[JudgeIssue]:
    """Beats should be in ascending order within each scene."""
    issues = []
    for scene in spec.scenes:
        orders = [b.order for b in scene.beats]
        if orders != sorted(orders):
            issues.append(JudgeIssue(
                severity="error",
                scene_id=scene.id,
                beat_id=None,
                category="ordering",
                message=f"Beat ordering is not monotonically increasing: {orders}",
            ))
    return issues


def _check_scene_ordering(spec: VideoSpec) -> list[JudgeIssue]:
    """Scenes should be in ascending order."""
    issues = []
    orders = [s.order for s in spec.scenes]
    if orders != sorted(orders):
        issues.append(JudgeIssue(
            severity="error",
            scene_id=None,
            beat_id=None,
            category="ordering",
            message=f"Scene ordering is not monotonically increasing: {orders}",
        ))
    return issues


def _check_visual_feasibility(spec: VideoSpec) -> list[JudgeIssue]:
    """Warn if visual_spec contains terms unlikely to be Manim-renderable."""
    issues = []
    infeasible_keywords = [
        "photograph", "real-world footage", "live action", "stock video",
        "screen recording", "screenshot", "3D render", "photorealistic",
        "video clip of", "actual footage",
    ]
    for scene in spec.scenes:
        if scene.visual_type not in (VisualType.manim, VisualType.chart):
            continue
        spec_lower = scene.visual_spec.lower()
        for keyword in infeasible_keywords:
            if keyword in spec_lower:
                issues.append(JudgeIssue(
                    severity="error",
                    scene_id=scene.id,
                    beat_id=None,
                    category="feasibility",
                    message=f"visual_spec contains '{keyword}' — not feasible for Manim animation",
                ))
    return issues


def _check_empty_fields(spec: VideoSpec) -> list[JudgeIssue]:
    """Critical fields must not be empty."""
    issues = []
    for scene in spec.scenes:
        if not scene.narration.strip():
            issues.append(JudgeIssue(
                severity="error",
                scene_id=scene.id,
                beat_id=None,
                category="visual_spec",
                message="Scene narration is empty",
            ))
        if scene.visual_type in (VisualType.manim, VisualType.chart) and not scene.visual_spec.strip():
            issues.append(JudgeIssue(
                severity="error",
                scene_id=scene.id,
                beat_id=None,
                category="visual_spec",
                message="Scene has visual_type=manim but empty visual_spec",
            ))
        for beat in scene.beats:
            if not beat.visual_action.strip():
                issues.append(JudgeIssue(
                    severity="warning",
                    scene_id=scene.id,
                    beat_id=beat.id,
                    category="visual_spec",
                    message="Beat has empty visual_action",
                ))
    return issues


def _check_beat_count(spec: VideoSpec) -> list[JudgeIssue]:
    """Scenes should have between 2-8 beats for proper pacing."""
    issues = []
    for scene in spec.scenes:
        if scene.visual_type not in (VisualType.manim, VisualType.chart):
            continue
        n = len(scene.beats)
        if n == 0:
            issues.append(JudgeIssue(
                severity="error",
                scene_id=scene.id,
                beat_id=None,
                category="coverage",
                message="Manim scene has no beats — codegen requires at least 2 beats",
            ))
        elif n == 1:
            issues.append(JudgeIssue(
                severity="warning",
                scene_id=scene.id,
                beat_id=None,
                category="coverage",
                message="Scene has only 1 beat — consider splitting for better pacing",
            ))
        elif n > 8:
            issues.append(JudgeIssue(
                severity="warning",
                scene_id=scene.id,
                beat_id=None,
                category="coverage",
                message=f"Scene has {n} beats (>8) — may be too complex for a single Manim scene",
            ))
    return issues


# ── Structural judge (deterministic) ──────────────────────────────────────────

def judge_structural(spec: VideoSpec) -> JudgeResult:
    """Run all deterministic checks on the VideoSpec. No LLM required."""
    all_issues: list[JudgeIssue] = []
    all_issues.extend(_check_trigger_phrases(spec))
    all_issues.extend(_check_narration_coverage(spec))
    all_issues.extend(_check_beat_ordering(spec))
    all_issues.extend(_check_scene_ordering(spec))
    all_issues.extend(_check_visual_feasibility(spec))
    all_issues.extend(_check_empty_fields(spec))
    all_issues.extend(_check_beat_count(spec))

    error_count = sum(1 for i in all_issues if i.severity == "error")
    passed = error_count == 0

    return JudgeResult(passed=passed, issues=all_issues)


# ── LLM-based feasibility judge ──────────────────────────────────────────────

_FEASIBILITY_JUDGE_SYSTEM = """\
You are a Manim animation feasibility expert. You evaluate whether visual_spec descriptions
can be implemented as Manim Community Edition animations (2D math/geometry/charts only).

You assess:
1. Is this purely mathematical/geometric/chart content? (Manim can't do photos, 3D renders, etc.)
2. Is the complexity reasonable for a single Manim scene? (max 6-8 objects, 80s animation)
3. Are the visual transitions clear enough for a developer to implement?
4. Does the visual match the narration intent?

Return valid JSON only.
"""

_FEASIBILITY_JUDGE_PROMPT = """\
Evaluate whether each scene's visual_spec is feasible for Manim CE animation.

Video topic: {topic}

Scenes to evaluate:
{scenes_json}

For each scene, determine:
- feasible: true/false — can this be built in Manim CE (2D math, geometry, charts)?
- complexity: "low" | "medium" | "high" — how hard is this to animate?
- issues: list of specific problems (empty if feasible and reasonable)
- suggestion: how to simplify or fix if not feasible (empty string if fine)

Return JSON:
{{
  "evaluations": [
    {{
      "scene_id": "s01",
      "feasible": true,
      "complexity": "medium",
      "issues": [],
      "suggestion": ""
    }}
  ],
  "overall_passed": true
}}
"""


async def judge_feasibility(spec: VideoSpec) -> JudgeResult:
    """LLM-based evaluation of whether visual_specs are Manim-feasible."""
    llm = get_llm_provider()

    scenes_for_eval = []
    for scene in spec.scenes:
        if scene.visual_type not in (VisualType.manim, VisualType.chart):
            continue
        scenes_for_eval.append({
            "scene_id": scene.id,
            "visual_spec": scene.visual_spec,
            "visual_type": scene.visual_type.value,
            "narration_preview": scene.narration[:200],
            "n_beats": len(scene.beats),
            "beat_actions": [b.visual_action for b in scene.beats],
        })

    if not scenes_for_eval:
        return JudgeResult(passed=True)

    prompt = _FEASIBILITY_JUDGE_PROMPT.format(
        topic=spec.topic,
        scenes_json=json.dumps(scenes_for_eval, ensure_ascii=False, indent=2),
    )

    try:
        resp = await llm.complete(
            [LLMMessage(role="user", content=prompt)],
            system=_FEASIBILITY_JUDGE_SYSTEM,
            max_tokens=4096,
            temperature=0,
        )

        # Parse response
        content = resp.content.strip()
        # Strip markdown fences if present
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        data = json.loads(content)
        evaluations = data.get("evaluations", [])

        issues = []
        for ev in evaluations:
            if not ev.get("feasible", True):
                issues.append(JudgeIssue(
                    severity="error",
                    scene_id=ev["scene_id"],
                    beat_id=None,
                    category="feasibility",
                    message=f"LLM judge: not feasible — {'; '.join(ev.get('issues', []))}",
                ))
            elif ev.get("complexity") == "high":
                issues.append(JudgeIssue(
                    severity="warning",
                    scene_id=ev["scene_id"],
                    beat_id=None,
                    category="feasibility",
                    message=f"High complexity — consider simplifying: {ev.get('suggestion', '')}",
                ))

        error_count = sum(1 for i in issues if i.severity == "error")
        return JudgeResult(passed=error_count == 0, issues=issues)

    except (json.JSONDecodeError, KeyError) as e:
        log.warning("spec_judge.feasibility_parse_error", error=str(e))
        # Don't block pipeline on judge parse errors
        return JudgeResult(passed=True, issues=[JudgeIssue(
            severity="warning",
            scene_id=None,
            beat_id=None,
            category="feasibility",
            message=f"Feasibility judge response could not be parsed: {e}",
        )])


# ── Reflection generator ─────────────────────────────────────────────────────

_REFLECT_SYSTEM = """\
You are a video script quality analyst. Given validation issues found in a VideoSpec,
produce actionable reflection that a script-writing agent can use to fix the problems.

Be specific: quote the problematic text, explain WHY it's wrong, and suggest a concrete fix.
Keep reflection concise (under 500 words).
"""

_REFLECT_PROMPT = """\
The following VideoSpec for topic "{topic}" failed validation with these issues:

{issues_text}

VideoSpec summary:
- {n_scenes} scenes, {n_beats} total beats
- Language: {language}
- Scenes: {scene_summaries}

Produce a concise reflection that a script agent can use to regenerate a better VideoSpec.
Focus on:
1. Which specific scenes/beats need fixing and HOW
2. Patterns across issues (systemic problems)
3. Concrete suggestions for improvement

Return plain text reflection (no JSON).
"""


async def reflect_on_issues(spec: VideoSpec, issues: list[JudgeIssue]) -> str:
    """Generate actionable reflection from validation issues for script regeneration."""
    llm = get_llm_provider()

    issues_text = "\n".join(
        f"- [{i.severity.upper()}] scene={i.scene_id} beat={i.beat_id} "
        f"category={i.category}: {i.message}"
        for i in issues
    )

    scene_summaries = "; ".join(
        f"{s.id}: '{s.visual_spec[:80]}…' ({len(s.beats)} beats)"
        for s in spec.scenes
    )

    prompt = _REFLECT_PROMPT.format(
        topic=spec.topic,
        issues_text=issues_text,
        n_scenes=len(spec.scenes),
        n_beats=sum(len(s.beats) for s in spec.scenes),
        language=spec.language,
        scene_summaries=scene_summaries,
    )

    resp = await llm.complete(
        [LLMMessage(role="user", content=prompt)],
        system=_REFLECT_SYSTEM,
        max_tokens=2000,
        temperature=0.3,
    )
    return resp.content


# ── Full judge + reflect pipeline ─────────────────────────────────────────────

async def judge_spec(spec: VideoSpec, *, include_llm_feasibility: bool = True) -> JudgeResult:
    """Full two-step judge: structural checks + optional LLM feasibility check.

    Returns a JudgeResult with all issues. If failed, includes a reflection
    string that the script agent can use for regeneration.
    """
    # Step 1: Structural (deterministic)
    structural = judge_structural(spec)

    # Step 2: LLM feasibility (optional, costs tokens)
    feasibility_issues: list[JudgeIssue] = []
    if include_llm_feasibility and structural.error_count == 0:
        # Only run LLM judge if structural passes — no point burning tokens on broken specs
        feasibility_result = await judge_feasibility(spec)
        feasibility_issues = feasibility_result.issues

    # Combine
    all_issues = structural.issues + feasibility_issues
    error_count = sum(1 for i in all_issues if i.severity == "error")
    passed = error_count == 0

    result = JudgeResult(passed=passed, issues=all_issues)

    # Generate reflection if failed
    if not passed:
        result.reflection = await reflect_on_issues(spec, all_issues)

    log.info(
        "spec_judge.result",
        topic=spec.topic,
        passed=passed,
        errors=error_count,
        warnings=sum(1 for i in all_issues if i.severity == "warning"),
    )
    return result
