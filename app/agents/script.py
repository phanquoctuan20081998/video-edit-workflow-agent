"""Stage 2 — Script research agent.

research(topic) → outline → write VideoSpec

Output: VideoSpec with scenes[] populated (narration + visual_type + visual_spec).
Does NOT set manim_code, clip_path, duration_sec (later stages own those).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from uuid import uuid4

import httpx
import structlog

from app.models.video_spec import Scene, VideoSpec, VisualType
from app.providers.base import LLMMessage
from app.providers.factory import get_llm_provider

log = structlog.get_logger()

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
"""

_OUTLINE_PROMPT = """\
Based on this research about "{topic}":

{research}

Create a scene-by-scene outline for a 3-5 minute explainer video. Each scene should:
- Have a clear visual purpose (what Manim animation will show)
- Have a narration beat (what the narrator says in ~15-30 seconds)
- Build on the previous scene

Output JSON array of scenes:
[
  {{
    "id": "s01",
    "order": 1,
    "narration": "...",
    "visual_type": "manim",
    "visual_spec": "describe the specific animation: objects, transforms, emphasis points"
  }},
  ...
]

visual_type options: manim (math/geometry), chart (data/bar chart), title_card (intro/outro), static_image.
Use "manim" for anything involving math, geometry, vectors, functions, transformations.
"""

_SCRIPT_REFINE_PROMPT = """\
Refine this video script outline for the topic "{topic}".

Current outline:
{outline_json}

Requirements:
- Each narration should be natural spoken language ({language})
- visual_spec must be detailed enough for a Manim developer to implement
- Ensure logical flow from scene to scene
- Keep each narration 15-30 seconds when spoken aloud
- Total video: 3-5 minutes

Return the refined JSON array only.
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

    async def outline(self, topic: str, research: str) -> list[dict]:
        """Generate scene-by-scene outline from research."""
        prompt = _OUTLINE_PROMPT.format(topic=topic, research=research)
        resp = await self._llm.complete(
            [LLMMessage(role="user", content=prompt)],
            system=_OUTLINE_SYSTEM,
            max_tokens=3000,
            temperature=0.5,
        )
        scenes_data = _parse_json_array(resp.content)
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
            max_tokens=3000,
            temperature=0.3,
        )
        refined = _parse_json_array(resp.content)

        scenes = []
        for i, raw in enumerate(refined):
            visual_type = VisualType(raw.get("visual_type", "manim"))
            scenes.append(Scene(
                id=raw.get("id", f"s{i+1:02d}"),
                order=raw.get("order", i + 1),
                narration=raw.get("narration", ""),
                visual_type=visual_type,
                visual_spec=raw.get("visual_spec", ""),
            ))

        spec = VideoSpec(
            topic=topic,
            language=language,
            source_refs=[s.url for s in sources if s.url],
            scenes=scenes,
        )
        log.info("script.spec_emitted", project_id=spec.project_id, scenes=len(scenes))
        return spec

    async def run(self, topic: str, language: str = "vi") -> VideoSpec:
        """Full pipeline: research → outline → VideoSpec."""
        research, sources = await self.research(topic)
        outline = await self.outline(topic, research)
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


def _parse_json_array(text: str) -> list[dict]:
    """Extract JSON array from LLM response (may be wrapped in markdown)."""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        log.warning("script.json_parse_failed", preview=text[:200])
        return []
    try:
        return json.loads(match.group())
    except json.JSONDecodeError as e:
        log.error("script.json_decode_error", error=str(e))
        return []
