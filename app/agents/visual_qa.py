"""Visual QA — checks rendered Manim frames against scene intent AND 3b1b style rules."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import structlog

from app.providers.base import LLMMessage
from app.providers.factory import get_vision_provider

log = structlog.get_logger()

_QA_SYSTEM = """\
You are a strict visual quality reviewer for math/physics explainer video animations
in the style of 3Blue1Brown. You check both correctness (does it show the right thing?)
and visual quality (is it clean, purposeful, non-slop?).
"""

_QA_PROMPT = """\
Scene intent: {intent}
Narration context: {narration}

Review the provided animation frames on TWO dimensions:

── DIMENSION 1: CORRECTNESS ──
- Are the intended mathematical objects present?
- Does the animation represent the stated intent?
- Any LaTeX render errors (□ boxes, missing symbols)?
- Any objects cropped by the frame edge?
- Does the animation appear to complete (no freeze mid-motion)?

── DIMENSION 2: 3B1B VISUAL QUALITY ──
Check for these specific style violations:

Background:
- [ ] Background is dark navy (~#1C1C2E), NOT pure black or white

Color discipline:
- [ ] No rainbow/arbitrary colors — colors carry semantic meaning
- [ ] Primary objects in blue tones, emphasis in yellow/gold, negation in red
- [ ] Axes/grids are subdued (dark teal), never dominant

Typography:
- [ ] Mathematical expressions use LaTeX (MathTex), not plain text
- [ ] No formula exceeds ~70% of frame width
- [ ] Text is readable (white/light on dark background)

Layout:
- [ ] No overlapping objects or text
- [ ] Objects have breathing room (not crammed to edges)
- [ ] No more than ~8 objects visible simultaneously

Animation quality:
- [ ] Objects don't all appear at once — sequential reveals evident
- [ ] Math formulas drawn stroke-by-stroke (Write animation), not popped in
- [ ] Visible pauses between reveals (not a rushed slideshow)
- [ ] At least one object transforms, moves, or changes color to teach something

Anti-slop check:
- [ ] NOT a static image with text — there must be meaningful animation
- [ ] NOT random floating objects with no relationship to each other
- [ ] Colors are NOT all the same (monochrome objects with no differentiation)

Respond with ONLY valid minified JSON. Keep every issue under 120 characters:
{{
  "passed": true/false,
  "correctness_issues": ["issue 1", ...],
  "style_issues": ["style violation 1", ...],
  "issues": ["all issues combined for repair prompt"]
}}

Pass only if BOTH dimensions are acceptable. A mathematically correct but visually
slop scene should fail with style_issues listed.
"""


@dataclass
class QAResult:
    passed: bool
    issues: list[str]
    correctness_issues: list[str]
    style_issues: list[str]
    raw_response: str = ""


async def vision_qa(
    frame_paths: list[str],
    intent: str,
    narration: str,
) -> QAResult:
    """Run visual QA on sampled frames. Returns QAResult."""
    if not frame_paths:
        log.warning("visual_qa.no_frames")
        return QAResult(passed=False, issues=["No frames to evaluate"],
                        correctness_issues=[], style_issues=[])

    provider = get_vision_provider()
    prompt = _QA_PROMPT.format(intent=intent, narration=narration)

    try:
        resp = await provider.vision_complete(
            messages=[LLMMessage(role="user", content=prompt)],
            image_paths=frame_paths,
            max_tokens=2048,
        )
    except NotImplementedError:
        log.warning("visual_qa.no_vision_support")
        return QAResult(passed=True, issues=[], correctness_issues=[],
                        style_issues=[], raw_response="Vision not supported — skipping QA")

    raw = resp.content
    result = _parse_qa_response(raw)
    log.info(
        "visual_qa.result",
        passed=result.passed,
        correctness_issues=result.correctness_issues,
        style_issues=result.style_issues,
    )
    return result


def _parse_qa_response(raw: str) -> QAResult:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return QAResult(
            passed=False,
            issues=[f"Visual QA parse error: could not find JSON object. Review rendered clip manually."],
            correctness_issues=[],
            style_issues=[],
            raw_response=raw,
        )
    try:
        data = json.loads(match.group())
        correctness = data.get("correctness_issues", [])
        style = data.get("style_issues", [])
        all_issues = data.get("issues", correctness + style)
        return QAResult(
            passed=bool(data.get("passed", False)),
            issues=all_issues,
            correctness_issues=correctness,
            style_issues=style,
            raw_response=raw,
        )
    except json.JSONDecodeError as e:
        return QAResult(
            passed=False,
            issues=[f"Visual QA parse error: {e}. Review rendered clip manually."],
            correctness_issues=[],
            style_issues=[],
            raw_response=raw,
        )
