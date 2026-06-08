"""Visual QA — checks rendered Manim frames against scene intent using a vision LLM."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import structlog

from app.providers.base import LLMMessage
from app.providers.factory import get_vision_provider

log = structlog.get_logger()

_QA_SYSTEM = """\
You are a visual quality reviewer for math/physics explainer video animations.
You receive frames from a Manim-rendered animation and check whether they correctly
represent the stated visual intent. Be strict — if something is wrong, list it.
"""

_QA_PROMPT = """\
Scene intent: {intent}
Narration context: {narration}

Review the provided frames and answer:
1. Do the frames correctly represent the intent?
2. Are there any visual problems (overlapping objects, clipped text, LaTeX errors,
   missing elements, animation frozen mid-way, layout issues)?

Respond with ONLY valid JSON:
{{"passed": true/false, "issues": ["problem 1", "problem 2"]}}
"""


@dataclass
class QAResult:
    passed: bool
    issues: list[str]
    raw_response: str = ""


async def vision_qa(
    frame_paths: list[str],
    intent: str,
    narration: str,
) -> QAResult:
    """Run visual QA on sampled frames. Returns QAResult."""
    if not frame_paths:
        log.warning("visual_qa.no_frames")
        return QAResult(passed=False, issues=["No frames to evaluate"])

    provider = get_vision_provider()
    prompt = _QA_PROMPT.format(intent=intent, narration=narration)

    try:
        resp = await provider.vision_complete(
            messages=[LLMMessage(role="user", content=prompt)],
            image_paths=frame_paths,
            max_tokens=512,
        )
    except NotImplementedError:
        log.warning("visual_qa.no_vision_support")
        return QAResult(passed=True, issues=[], raw_response="Vision not supported — skipping QA")

    raw = resp.content
    result = _parse_qa_response(raw)
    log.info("visual_qa.result", passed=result.passed, issues=result.issues)
    return result


def _parse_qa_response(raw: str) -> QAResult:
    # Extract JSON from markdown code block if present
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return QAResult(passed=False, issues=[f"Could not parse QA response: {raw[:200]}"], raw_response=raw)
    try:
        data = json.loads(match.group())
        return QAResult(
            passed=bool(data.get("passed", False)),
            issues=data.get("issues", []),
            raw_response=raw,
        )
    except json.JSONDecodeError as e:
        return QAResult(passed=False, issues=[f"JSON parse error: {e}"], raw_response=raw)
