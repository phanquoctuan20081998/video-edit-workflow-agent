"""Stage 2 — Script research agent.

research(topic) → outline → write VideoSpec (with beat segmentation)

Output: VideoSpec with scenes[] populated (narration + visual_type + visual_spec + beats[]).
Each scene is a "chapter" (~1-3 min) with multiple beats for intra-scene sync.
Does NOT set manim_code, clip_path, duration_sec (later stages own those).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from uuid import uuid4

import httpx
import structlog

from app.models.video_spec import Beat, Scene, VideoSpec, VisualType
from app.providers.base import LLMMessage
from app.providers.factory import get_llm_provider

log = structlog.get_logger()


class ScriptGenerationError(RuntimeError):
    """Raised when the LLM response cannot be turned into a usable VideoSpec."""

_RESEARCH_SYSTEM = """\
You are a scientific researcher and educator specializing in math, physics, and algorithms.
You research topics thoroughly and write clear, accurate explanations suitable for video narration.
"""

_RESEARCH_PROMPT = """\
Research the topic: "{topic}"

Provide a comprehensive overview covering:
1. Core concept and definition
2. Key mathematical ideas (equations, theorems)
3. Visual intuition (what can be animated)
4. Real-world applications
5. Common misconceptions to address

Be factual and precise. Include specific equations where relevant.
"""

_OUTLINE_SYSTEM = """\
You are a video script writer for math/physics explainer videos in the style of 3Blue1Brown.
You create scene-by-scene outlines that build intuition progressively.
You return valid JSON only: no markdown, no prose, no comments.
"""

_OUTLINE_PROMPT = """\
Based on this research about "{topic}":

{research}

Create a scene-by-scene outline for a 3-5 minute explainer video. Each scene is a
"chapter" lasting 30-120 seconds with MULTIPLE visual beats that flow continuously.

KEY PRINCIPLE: Within a scene, objects persist and transform — no hard cuts between beats.
Each beat = one visual transition. The animation is continuous (like 3Blue1Brown).

LANGUAGE REQUIREMENT: Write ALL narration text in "{language}". This is critical — the
narration will be passed directly to a TTS engine that speaks in "{language}".

Output JSON array of scenes:
[
  {{
    "id": "s01",
    "order": 1,
    "narration": "Full narration in {language} for the entire scene/chapter...",
    "visual_type": "manim",
    "visual_spec": "Overall visual description of the continuous animation",
    "beats": [
      {{
        "id": "s01_b01",
        "order": 1,
        "trigger_phrase": "exact substring from narration that starts this beat (in {language})",
        "visual_action": "describe what Manim animates: Create/Transform/FadeOut/etc",
        "narration_segment": "the portion of narration this beat covers (in {language})"
      }},
      {{
        "id": "s01_b02",
        "order": 2,
        "trigger_phrase": "another exact substring (in {language})",
        "visual_action": "next animation step, building on previous objects",
        "narration_segment": "next portion of narration (in {language})"
      }}
    ]
  }},
  ...
]

RULES:
- Each scene has 3-8 beats (fewer for simple concepts, more for complex ones)
- trigger_phrase MUST be an exact substring of the scene's narration
- Beats should be ordered to match narration flow
- visual_action describes WHAT changes, referencing objects from previous beats
- narration_segments concatenated = full narration (no gaps, no overlap)
- visual_type: manim (math/geometry), chart (data), title_card (intro/outro)
- narration, trigger_phrase, narration_segment MUST all be in "{language}"
- Return ONLY valid JSON. Do not wrap it in markdown.
- Escape any double quotes inside strings. Prefer plain-text math like TT-star over LaTeX.
"""

_SCRIPT_REFINE_PROMPT = """\
Refine this video script outline for the topic "{topic}".

Current outline:
{outline_json}

Requirements:
- Each narration should be natural spoken language ({language})
- visual_spec must be detailed enough for a Manim developer to implement
- Ensure logical flow from scene to scene
- Each scene is a chapter (30-120 seconds when spoken)
- Each beat's trigger_phrase must be an EXACT substring of the scene narration
- Beat narration_segments must cover the full narration without gaps
- visual_action should reference objects created in earlier beats within the same scene
- Total video: 3-5 minutes

Return the refined JSON array only (same structure with scenes and beats).
Return ONLY valid JSON. Do not wrap it in markdown. Escape any double quotes inside strings.
Prefer plain-text math like TT-star over LaTeX when it avoids JSON escaping issues.
"""

_JSON_REPAIR_SYSTEM = """\
You repair malformed JSON from a video-script generator.
Return only valid JSON. Do not add markdown, explanations, or comments.
"""

_JSON_REPAIR_PROMPT = """\
The following text was intended to be a JSON array of scene objects, but it is malformed.

Repair it into a valid JSON array only. Preserve as much content as possible.
If a string contains unescaped quotes or math notation, rewrite it safely as plain text.

Malformed JSON/text:
{text}
"""


@dataclass
class Source:
    title: str
    url: str
    content: str
    source_type: str   # "arxiv" | "web"


class ScriptAgent:
    def __init__(self, llm=None):
        self._llm = llm or get_llm_provider()

    async def research(self, topic: str) -> tuple[str, list[Source]]:
        """Fetch sources and synthesize research summary."""
        sources = await _fetch_sources(topic)
        source_text = "\n\n".join(f"[{s.source_type}] {s.title}\n{s.content[:800]}" for s in sources)

        prompt = _RESEARCH_PROMPT.format(topic=topic)
        if source_text:
            prompt += f"\n\nRelevant sources:\n{source_text}"

        resp = await self._llm.complete(
            [LLMMessage(role="user", content=prompt)],
            system=_RESEARCH_SYSTEM,
            max_tokens=2000,
        )
        log.info("script.research_done", topic=topic, sources=len(sources))
        return resp.content, sources

    async def outline(self, topic: str, research: str, language: str = "en") -> list[dict]:
        """Generate scene-by-scene outline from research."""
        prompt = _OUTLINE_PROMPT.format(topic=topic, research=research, language=language)
        resp = await self._llm.complete(
            [LLMMessage(role="user", content=prompt)],
            system=_OUTLINE_SYSTEM,
            max_tokens=5000,
            temperature=0.5,
        )
        scenes_data = await self._parse_or_repair_json_array(resp.content, step="outline")
        if not scenes_data:
            raise ScriptGenerationError(
                "The outline step returned no scenes. Check the LLM response format or model output."
            )
        log.info("script.outline_done", topic=topic, scenes=len(scenes_data))
        return scenes_data

    async def write_spec(
        self,
        topic: str,
        outline: list[dict],
        sources: list[Source],
        language: str = "vi",
    ) -> VideoSpec:
        """Refine outline and emit VideoSpec."""
        prompt = _SCRIPT_REFINE_PROMPT.format(
            topic=topic,
            outline_json=json.dumps(outline, ensure_ascii=False, indent=2),
            language=language,
        )
        resp = await self._llm.complete(
            [LLMMessage(role="user", content=prompt)],
            system=_OUTLINE_SYSTEM,
            max_tokens=8000,
            temperature=0.3,
        )
        refined = await self._parse_or_repair_json_array(resp.content, step="refine")
        if not refined:
            raise ScriptGenerationError(
                "The script refinement step returned no scenes. Check the LLM response format or model output."
            )

        scenes = []
        for i, raw in enumerate(refined):
            visual_type = VisualType(raw.get("visual_type", "manim"))

            # Parse beats
            beats = []
            for j, beat_raw in enumerate(raw.get("beats", [])):
                beats.append(Beat(
                    id=beat_raw.get("id", f"s{i+1:02d}_b{j+1:02d}"),
                    order=beat_raw.get("order", j + 1),
                    trigger_phrase=beat_raw.get("trigger_phrase", ""),
                    visual_action=beat_raw.get("visual_action", ""),
                    narration_segment=beat_raw.get("narration_segment", ""),
                ))

            scenes.append(Scene(
                id=raw.get("id", f"s{i+1:02d}"),
                order=raw.get("order", i + 1),
                narration=raw.get("narration", ""),
                visual_type=visual_type,
                visual_spec=raw.get("visual_spec", ""),
                beats=beats,
            ))

        if not scenes:
            raise ScriptGenerationError("No scenes were generated for the VideoSpec.")

        spec = VideoSpec(
            topic=topic,
            language=language,
            source_refs=[s.url for s in sources if s.url],
            scenes=scenes,
        )
        log.info("script.spec_emitted", project_id=spec.project_id, scenes=len(scenes),
                 total_beats=sum(len(s.beats) for s in scenes))
        return spec

    async def _parse_or_repair_json_array(self, text: str, *, step: str) -> list[dict]:
        if err := _detect_provider_error(text):
            raise ScriptGenerationError(f"{step} failed: {err}. Check provider status and retry.")

        try:
            parsed = _parse_json_array(text)
        except ScriptGenerationError as first_error:
            log.warning("script.json_repair_attempt", step=step, error=str(first_error))
        else:
            if parsed:
                return parsed
            log.warning("script.json_repair_attempt", step=step, error="parsed empty scene list")

        repair_prompt = _JSON_REPAIR_PROMPT.format(text=text[:30000])
        resp = await self._llm.complete(
            [LLMMessage(role="user", content=repair_prompt)],
            system=_JSON_REPAIR_SYSTEM,
            max_tokens=8000,
            temperature=0,
        )
        repaired = _parse_json_array(resp.content)
        if repaired:
            log.info("script.json_repair_done", step=step, scenes=len(repaired))
            return repaired

        raise ScriptGenerationError(f"The {step} step could not be repaired into scene JSON.")

    async def run(self, topic: str, language: str = "en") -> VideoSpec:
        """Full pipeline: research → outline → VideoSpec."""
        research, sources = await self.research(topic)
        outline = await self.outline(topic, research, language=language)
        return await self.write_spec(topic, outline, sources, language=language)


# ── Source fetching ────────────────────────────────────────────────────────────

async def _fetch_sources(topic: str) -> list[Source]:
    sources: list[Source] = []

    # arXiv search
    try:
        import arxiv
        client = arxiv.Client()
        search = arxiv.Search(query=topic, max_results=3, sort_by=arxiv.SortCriterion.Relevance)
        for paper in client.results(search):
            sources.append(Source(
                title=paper.title,
                url=str(paper.entry_id),
                content=paper.summary,
                source_type="arxiv",
            ))
    except Exception as e:
        log.warning("script.arxiv_failed", error=str(e))

    return sources


def _detect_provider_error(text: str) -> str | None:
    """Return a short description if text is a provider error response, else None."""
    t = text.strip()
    if re.search(r"""['"]error['"]\s*:""", t):
        code = re.search(r"""['"]code['"]\s*:\s*(\d+)""", t)
        msg  = re.search(r"""['"]message['"]\s*:\s*['"]([^'"]{0,120})""", t)
        code_str = code.group(1) if code else "unknown"
        msg_str  = msg.group(1)  if msg  else t[:120]
        log.error("script.provider_error", code=code_str, message=msg_str)
        return f"HTTP {code_str}: {msg_str}"
    return None


def _parse_json_array(text: str) -> list[dict]:
    """Extract scene objects from an LLM response.

    Accepts a bare JSON array, a fenced JSON block, or an object containing
    a ``scenes``/``outline`` array. Raises instead of silently returning an
    empty script so the UI can show a real error.
    """
    try:
        parsed = _parse_json_payload(text)
    except json.JSONDecodeError as e:
        log.error("script.json_decode_error", error=str(e), preview=text[:500])
        raise ScriptGenerationError(
            f"Could not parse script JSON from LLM response: {e.msg}"
        ) from e

    if isinstance(parsed, list):
        scenes = parsed
    elif isinstance(parsed, dict):
        scenes = _first_scene_array(parsed)
    else:
        scenes = []

    if not scenes:
        log.warning("script.json_parse_empty", preview=text[:500])
        return []

    return [scene for scene in scenes if isinstance(scene, dict)]


def _parse_json_payload(text: str):
    for fenced in re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE):
        stripped = fenced.strip()
        if stripped:
            return json.loads(stripped)

    stripped = text.strip()
    if not stripped:
        raise json.JSONDecodeError("No JSON object or array found", text, 0)

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    start_positions = [pos for pos in (stripped.find("["), stripped.find("{")) if pos >= 0]
    if not start_positions:
        raise json.JSONDecodeError("No JSON object or array found", text, 0)

    start = min(start_positions)
    candidate = stripped[start:]

    # Only attempt to parse the first apparent top-level payload. If it is
    # malformed, raise so the repair pass can fix the original response rather
    # than accidentally parsing a nested []/{} later in the text.
    decoder = json.JSONDecoder()
    parsed, _ = decoder.raw_decode(candidate)
    return parsed


def _first_scene_array(payload: dict) -> list:
    for key in ("scenes", "outline", "script", "video_spec"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _first_scene_array(value)
            if nested:
                return nested
    return []
