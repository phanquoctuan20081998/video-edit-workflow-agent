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
You are a scientific storyteller — part researcher, part educator — specializing in math, \
physics, and algorithms. You find the human story inside every concept: the problem it solved, \
the moment of insight, the surprising connection. Your explanations feel like a conversation \
with a brilliant friend, not a textbook.
"""

_RESEARCH_PROMPT = """\
Research the topic: "{topic}"

Tell the story of this concept, covering:
1. The problem or mystery that motivated it — why did anyone care?
2. The core idea explained through a concrete analogy or everyday example first
3. The key mathematical insight (equations come AFTER the intuition, not before)
4. A surprising or counterintuitive fact that makes you say "wait, really?"
5. Real-world impact — where does this actually show up in the world?
6. Common misconceptions and why they feel intuitive but are wrong

Write as if explaining to a curious, intelligent friend who has never studied this topic.
Use "you" and "we" to engage the reader. Short sentences. Active voice.
Be factual and precise, but lead with intuition, not formalism.
"""

_OUTLINE_SYSTEM = """\
You are a video script writer for math/physics explainer videos in the style of 3Blue1Brown.
You are a master storyteller: every video opens with a hook, builds mystery, then delivers the
"aha" moment. Narration sounds like natural spoken conversation — warm, curious, never dry.
You return valid JSON only: no markdown, no prose, no comments.
"""

_OUTLINE_PROMPT = """\
Based on this research about "{topic}":

{research}

Create a scene-by-scene outline for a {duration_target} explainer video. Each scene is a
"chapter" lasting 30-120 seconds with MULTIPLE visual beats that flow continuously.

LENGTH BUDGET (HARD REQUIREMENT):
- Target total video length: {duration_target}.
- Total narration across ALL scenes must be approximately {word_budget} words
  (TTS speaks ~{wpm} words/minute in "{language}"). Stay within ±10% of this budget.
- Distribute the word budget across scenes; state fewer ideas rather than rushing many.
</LENGTH BUDGET>

KEY PRINCIPLE: Within a scene, objects persist and transform — no hard cuts between beats.
Each beat = one visual transition. The animation is continuous (like 3Blue1Brown).

STORYTELLING PRINCIPLES — follow these for every scene:
- Scene 1 MUST open with a hook: a surprising question, a puzzle, or a relatable everyday
  situation that makes the viewer think "huh, I never thought about that."
- Use analogies before equations. Say "think of it like a spinning top" BEFORE showing math.
- Speak in "you" and "we": "You might think...", "What if we tried...", "Here's the twist."
- Build tension: introduce a problem or question early, let it breathe, then resolve it.
- Reveal the insight gradually — do NOT explain everything in the first beat.
- Short, punchy sentences. Avoid academic phrasing. No "therefore", "thus", "it is evident".
- End each scene with a satisfying payoff line or a teaser for the next scene.

LANGUAGE REQUIREMENT: Write ALL narration text in "{language}". This is critical — the
narration will be passed directly to a TTS engine that speaks in "{language}".

Output JSON array of scenes:
[
  {{
    "id": "s01",
    "order": 1,
    "narration": "Full narration in {language} for the entire scene/chapter. Should sound like natural spoken storytelling, not a lecture.",
    "visual_type": "manim",
    "visual_spec": "Overall visual description of the continuous animation",
    "beats": [
      {{
        "id": "s01_b01",
        "order": 1,
        "trigger_phrase": "exact substring from narration that starts this beat (in {language})",
        "visual_action": "describe what Manim animates: Create/Transform/FadeOut/etc",
        "narration_segment": "the portion of narration this beat covers (in {language})",
        "must_show": ["concrete visual object/change that must appear", "second required visual element"],
        "on_screen_label": "short label/formula to display, or empty string",
        "forbidden_visuals": ["visuals that would contradict this beat or confuse the concept"]
      }},
      {{
        "id": "s01_b02",
        "order": 2,
        "trigger_phrase": "another exact substring (in {language})",
        "visual_action": "next animation step, building on previous objects",
        "narration_segment": "next portion of narration (in {language})",
        "must_show": ["exact visual evidence for this spoken idea"],
        "on_screen_label": "short label/formula to display, or empty string",
        "forbidden_visuals": ["unrelated diagrams", "objects not mentioned by this beat"]
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
- must_show lists 1-3 CONCEPT-LEVEL visual elements that make the narration_segment
  visibly true on screen. Write what the viewer should understand, not pixel specs:
  GOOD: "accuracy bar drops noticeably", "grid grows denser", "two curves diverge"
  BAD: "needle drops from 90 to 60", "patches become blurry", "label shows '60%'"
  Never specify exact percentages, pixel effects (blur/glow), or verbatim label strings.
  Effects like blur, glow, pixelation are unreliable in Manim — use visual metaphors.
- on_screen_label is short: 1-6 words or one compact formula; use empty string if none
- forbidden_visuals lists visuals that would mismatch or overgeneralize this beat
- visual_type: manim (math/geometry), chart (data), title_card (intro/outro)
- narration, trigger_phrase, narration_segment, and on_screen_label MUST all be in "{language}"
- Return ONLY valid JSON. Do not wrap it in markdown.
- Escape any double quotes inside strings. Prefer plain-text math like TT-star over LaTeX.
"""

_SCRIPT_REFINE_PROMPT = """\
Refine this video script outline for the topic "{topic}".

Current outline:
{outline_json}

NARRATION QUALITY — these are the most important requirements:
- Every narration must sound like natural SPOKEN storytelling, not written prose or a lecture.
  Read each line aloud in your head. If it sounds stiff or academic, rewrite it.
- Use "you" and "we" throughout: "You might wonder...", "Let's try...", "Here's where it gets interesting."
- Analogies and concrete examples MUST come before any equation or abstract definition.
- Each scene should have a clear narrative arc: setup → tension → resolution (or teaser).
- Scene 1 must open with a compelling hook — a question, a paradox, or a surprising fact.
- End each scene on a satisfying note OR a cliffhanger that pulls into the next scene.
- Sentences should be short and punchy. Avoid: "therefore", "thus", "it is evident", "we can observe".
- If a concept is hard, use TWO analogies, not one. The second analogy is the insurance.

STRUCTURAL REQUIREMENTS:
- visual_spec must be detailed enough for a Manim developer to implement
- Ensure logical flow from scene to scene — each scene answers the question raised by the previous
- Each scene is a chapter (30-120 seconds when spoken)
- Each beat's trigger_phrase must be an EXACT substring of the scene narration
- Beat narration_segments must cover the full narration without gaps
- visual_action should reference objects created in earlier beats within the same scene
- Each beat must include must_show, on_screen_label, and forbidden_visuals
- must_show items must be CONCEPT-LEVEL (what concept is shown), not pixel-level specs.
  Write what the viewer should understand, not implementation details:
  GOOD: "accuracy bar drops noticeably", "grid grows denser", "two curves diverge"
  BAD: "needle drops from 90 to 60", "patches become blurry", "label shows '60%'"
  Never specify exact percentages, blur/glow effects, or verbatim label strings.
- Total video length: {duration_target}. Total narration across all scenes must be
  approximately {word_budget} words (±10%). Trim or expand narration to fit.

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
            max_tokens=32000,
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
        target_duration_sec: float | None = None,
    ) -> VideoSpec:
        """Refine outline and emit VideoSpec."""
        duration_target, word_budget, _wpm = _duration_budget(target_duration_sec, language)
        prompt = _SCRIPT_REFINE_PROMPT.format(
            topic=topic,
            outline_json=json.dumps(outline, ensure_ascii=False, indent=2),
            language=language,
            duration_target=duration_target,
            word_budget=word_budget,
        )
        resp = await self._llm.complete(
            [LLMMessage(role="user", content=prompt)],
            system=_OUTLINE_SYSTEM,
            max_tokens=65000,
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
                    must_show=_as_str_list(beat_raw.get("must_show", [])),
                    on_screen_label=beat_raw.get("on_screen_label") or None,
                    forbidden_visuals=_as_str_list(beat_raw.get("forbidden_visuals", [])),
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
            target_duration_sec=target_duration_sec,
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
            max_tokens=65000,
            temperature=0,
        )
        repaired = _parse_json_array(resp.content)
        if repaired:
            log.info("script.json_repair_done", step=step, scenes=len(repaired))
            return repaired

        raise ScriptGenerationError(f"The {step} step could not be repaired into scene JSON.")

    async def run(
        self,
        topic: str,
        language: str = "en",
        max_judge_reflections: int = 3,
        target_duration_sec: float | None = None,
    ) -> VideoSpec:
        """Full pipeline: research → outline → VideoSpec → judge → [reflect + retry].

        The judge + reflect loop validates the spec BEFORE expensive Manim codegen.
        If the spec fails validation, the reflection is fed back to the outline step
        to regenerate a better spec (up to max_judge_reflections times).
        """
        from app.agents.spec_judge import judge_spec

        research, sources = await self.research(topic)
        outline = await self.outline(
            topic, research, language=language, target_duration_sec=target_duration_sec,
        )
        spec = await self.write_spec(
            topic, outline, sources, language=language,
            target_duration_sec=target_duration_sec,
        )
        spec = await self._fit_to_duration(spec, topic, language)

        # Judge + reflect loop
        for attempt in range(max_judge_reflections):
            judge_result = await judge_spec(spec, include_llm_feasibility=True)
            if judge_result.passed:
                log.info("script.judge_passed", topic=topic, attempt=attempt)
                return spec

            log.warning(
                "script.judge_failed",
                topic=topic,
                attempt=attempt,
                errors=judge_result.error_count,
                warnings=judge_result.warning_count,
            )

            if not judge_result.reflection:
                break  # No actionable feedback — return best-effort

            # Feed reflection back into outline regeneration
            reflected_outline = await self._regenerate_with_reflection(
                topic, research, outline, judge_result.reflection, language=language,
            )
            if reflected_outline:
                outline = reflected_outline
                spec = await self.write_spec(
                    topic, outline, sources, language=language,
                    target_duration_sec=target_duration_sec,
                )
                spec = await self._fit_to_duration(spec, topic, language)

        log.warning("script.judge_cap_reached", topic=topic, max=max_judge_reflections)
        return spec


    async def _fit_to_duration(self, spec: VideoSpec, topic: str, language: str) -> VideoSpec:
        """If narration deviates >20% from target_duration_sec, rewrite narration to fit.

        One pass only — trims (or expands) each scene's narration while keeping
        beat structure intact. trigger_phrase / narration_segment are re-derived
        by the LLM so they stay exact substrings of the new narration.
        """
        target = spec.target_duration_sec
        if not target:
            return spec

        estimated = spec.estimated_duration_sec()
        if estimated <= 0:
            return spec
        drift = (estimated - target) / target
        if abs(drift) <= 0.20:
            log.info("script.duration_ok", estimated=estimated, target=target)
            return spec

        from app.models.video_spec import words_per_second
        word_budget = int(target * words_per_second(language))
        direction = "SHORTEN" if drift > 0 else "EXPAND"
        log.info("script.duration_fit", estimated=estimated, target=target, action=direction)

        prompt = f"""\
The following video script is estimated at {estimated:.0f}s but the target length is
{target:.0f}s. {direction} the narration of each scene so the TOTAL narration is
approximately {word_budget} words (current total: {sum(len(s.narration.split()) for s in spec.scenes)} words).

Rules:
- Keep the same scenes and the same beat structure (same beat ids and order).
- Rewrite narration naturally — do not just delete sentence halves.
- After rewriting, update each beat's trigger_phrase and narration_segment so that
  trigger_phrase is an EXACT substring of the new narration and the
  narration_segments concatenate to cover the full new narration.
- All text stays in "{language}".

Script JSON:
{{script_json}}

Return ONLY the corrected JSON array of scenes. No markdown fences.
"""
        import json as _json
        scenes_json = _json.dumps(
            [s.model_dump(include={"id", "order", "narration", "visual_type", "visual_spec", "beats"})
             for s in spec.scenes],
            ensure_ascii=False, default=str,
        )
        try:
            resp = await self._llm.complete(
                [LLMMessage(role="user", content=prompt.replace("{script_json}", scenes_json))],
                system=_OUTLINE_SYSTEM,
                max_tokens=65000,
                temperature=0.3,
            )
            fixed = await self._parse_or_repair_json_array(resp.content, step="duration_fit")
        except ScriptGenerationError as e:
            log.warning("script.duration_fit_failed", error=str(e))
            return spec
        if not fixed:
            return spec

        new_spec = await self._scenes_from_raw(fixed, spec)
        new_est = new_spec.estimated_duration_sec()
        log.info("script.duration_fit_done", old=estimated, new=new_est, target=target)
        return new_spec

    async def _scenes_from_raw(self, raw_scenes: list[dict], base_spec: VideoSpec) -> VideoSpec:
        """Rebuild scenes (with beats) from raw dicts, preserving spec metadata."""
        scenes = []
        for i, raw in enumerate(raw_scenes):
            beats = []
            for j, beat_raw in enumerate(raw.get("beats", [])):
                beats.append(Beat(
                    id=beat_raw.get("id", f"s{i+1:02d}_b{j+1:02d}"),
                    order=beat_raw.get("order", j + 1),
                    trigger_phrase=beat_raw.get("trigger_phrase", ""),
                    visual_action=beat_raw.get("visual_action", ""),
                    narration_segment=beat_raw.get("narration_segment", ""),
                    must_show=_as_str_list(beat_raw.get("must_show", [])),
                    on_screen_label=beat_raw.get("on_screen_label") or None,
                    forbidden_visuals=_as_str_list(beat_raw.get("forbidden_visuals", [])),
                ))
            scenes.append(Scene(
                id=raw.get("id", f"s{i+1:02d}"),
                order=raw.get("order", i + 1),
                narration=raw.get("narration", ""),
                visual_type=VisualType(raw.get("visual_type", "manim")),
                visual_spec=raw.get("visual_spec", ""),
                beats=beats,
            ))
        new_spec = base_spec.model_copy(deep=True)
        new_spec.scenes = scenes
        return new_spec

    async def _regenerate_with_reflection(
        self,
        topic: str,
        research: str,
        previous_outline: list[dict],
        reflection: str,
        language: str = "en",
    ) -> list[dict] | None:
        """Regenerate outline incorporating judge reflection feedback."""
        prompt = f"""\
Regenerate the scene outline for topic "{topic}" based on the following reflection
from a validation step that found problems in the previous version.

Previous outline (has issues):
{json.dumps(previous_outline, ensure_ascii=False, indent=2)[:10000]}

Reflection (issues to fix):
{reflection}

Research context:
{research[:3000]}

Fix ALL issues mentioned in the reflection. Ensure:
- Every trigger_phrase is an exact substring of its scene's narration
- Beat narration_segments concatenate to cover the full narration
- visual_spec is achievable with Manim CE (2D math, geometry, charts only)
- Beats are in order
- All narration is in "{language}"

Return the corrected JSON array of scenes (same format as before).
Return ONLY valid JSON. No markdown fences.
"""
        try:
            resp = await self._llm.complete(
                [LLMMessage(role="user", content=prompt)],
                system=_OUTLINE_SYSTEM,
                max_tokens=65000,
                temperature=0.3,
            )
            return await self._parse_or_repair_json_array(resp.content, step="judge_reflect")
        except ScriptGenerationError as e:
            log.warning("script.reflect_regen_failed", error=str(e))
            return None


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


def _as_str_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _duration_budget(target_duration_sec: float | None, language: str) -> tuple[str, int, int]:
    """Return (human-readable target, word budget, wpm) for prompt injection."""
    from app.models.video_spec import _LANG_WPM
    wpm = _LANG_WPM.get(language, 150)
    if not target_duration_sec:
        # Default behaviour unchanged: 3-5 minute video, budget at 4 min midpoint
        return "3-5 minute", wpm * 4, wpm
    minutes = target_duration_sec / 60.0
    label = f"~{minutes:.1f} minute ({target_duration_sec:.0f} second)"
    return label, int(minutes * wpm), wpm
