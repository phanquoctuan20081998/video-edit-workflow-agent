"""Template RAG — semantic retrieval of best-matching Manim templates for codegen.

Instead of having the LLM write free-form Manim code from scratch every time,
this module embeds template descriptions and retrieves the best match for a given
visual_spec. When a template matches well, codegen gets a concrete starting point
(template name + suggested params), drastically reducing errors and repair cycles.

Inspired by VideoAgent's "Storyboard Agent" which decomposes queries into
fine-grained sub-queries aligned with available video material.

Strategy:
  1. Each template in app/templates/ has a name + description.
  2. At retrieval time, decompose the visual_spec + beat actions into sub-queries.
  3. Score each template against each sub-query using keyword overlap + LLM ranking.
  4. Return top-K matches with confidence scores and suggested params.

No external vector DB — we use lightweight keyword matching + optional LLM reranking
since the template library is small (< 50 templates).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import structlog

from app.providers.base import LLMMessage
from app.providers.factory import get_llm_provider

log = structlog.get_logger()


def _get_template_registry():
    """Lazy-load template registry to ensure all submodules are imported."""
    import app.templates  # noqa: F401 — triggers submodule imports
    from app.templates.base import _REGISTRY
    return _REGISTRY


@dataclass
class TemplateMatch:
    """A template that might be useful for the given visual_spec."""
    template_name: str
    description: str
    confidence: float  # 0.0 - 1.0
    suggested_params: dict = field(default_factory=dict)
    reasoning: str = ""


# ── Keyword index for fast pre-filtering ──────────────────────────────────────

# Map of keywords → template names for rapid filtering
_KEYWORD_INDEX: dict[str, list[str]] = {}


def _build_keyword_index():
    """Build keyword index from template names and descriptions."""
    global _KEYWORD_INDEX
    _KEYWORD_INDEX = {}
    for name, cls in _get_template_registry().items():
        description = cls.description.lower()
        # Extract meaningful keywords
        words = set(re.findall(r'[a-z]{3,}', f"{name} {description}"))
        for word in words:
            if word not in _KEYWORD_INDEX:
                _KEYWORD_INDEX[word] = []
            _KEYWORD_INDEX[word].append(name)


def _keyword_score(query: str, template_name: str) -> float:
    """Score a template against a query using keyword overlap + synonym expansion."""
    if not _KEYWORD_INDEX:
        _build_keyword_index()

    query_words = set(re.findall(r'[a-z]{3,}', query.lower()))
    if not query_words:
        return 0.0

    cls = _get_template_registry().get(template_name)
    if not cls:
        return 0.0

    template_words = set(re.findall(r'[a-z]{3,}', f"{template_name} {cls.description}".lower()))

    # Direct overlap
    overlap = query_words & template_words

    # Synonym/related term expansion for common math/visual concepts
    _RELATED_TERMS = {
        "wave": {"waveform", "signal", "sinusoid", "frequency", "oscillat"},
        "waveform": {"wave", "signal", "sinusoid", "frequency"},
        "vector": {"arrow", "direction", "component", "field"},
        "matrix": {"multiplication", "eigenvalue", "linear", "transform"},
        "function": {"graph", "plot", "curve", "axes"},
        "graph": {"function", "plot", "curve", "axes"},
        "chart": {"bar", "data", "values", "categories"},
        "transform": {"rotation", "scale", "reflect", "geometry", "polygon"},
        "circle": {"angle", "theorem", "inscribed", "arc"},
        "polygon": {"triangle", "square", "hexagon", "geometry", "shape"},
        "frequency": {"wave", "signal", "spectrum", "fourier", "fft"},
        "fourier": {"frequency", "signal", "wave", "spectrum", "transform"},
        "sum": {"add", "combine", "vector", "component"},
        "phasor": {"wave", "signal", "frequency", "rotating", "vector"},
        "spectrum": {"frequency", "signal", "wave", "fourier"},
    }

    # Check if any query word has a related term in the template
    for qw in query_words:
        related = _RELATED_TERMS.get(qw, set())
        if related & template_words:
            overlap.add(qw)  # Count it as a match

    # Also check prefix matches (e.g., "rotat" matches "rotation")
    for qw in query_words:
        for tw in template_words:
            if len(qw) >= 4 and (qw.startswith(tw[:4]) or tw.startswith(qw[:4])):
                overlap.add(qw)
                break

    return len(overlap) / max(len(query_words), 1)


# ── Fast retrieval (no LLM, keyword-based) ────────────────────────────────────

def retrieve_templates_fast(
    visual_spec: str,
    beat_actions: list[str] | None = None,
    top_k: int = 3,
    min_score: float = 0.1,
) -> list[TemplateMatch]:
    """Fast keyword-based template retrieval. No LLM cost.

    Use this for a quick pre-filter before the full LLM-ranked retrieval,
    or when you want zero-cost template suggestions.
    """
    if not _KEYWORD_INDEX:
        _build_keyword_index()

    # Combine visual_spec + beat actions into one query
    query_parts = [visual_spec]
    if beat_actions:
        query_parts.extend(beat_actions)
    full_query = " ".join(query_parts)

    # Score all templates
    registry = _get_template_registry()
    scores: list[tuple[str, float]] = []
    for name in registry:
        score = _keyword_score(full_query, name)
        if score >= min_score:
            scores.append((name, score))

    # Sort by score descending
    scores.sort(key=lambda x: x[1], reverse=True)

    results = []
    for name, score in scores[:top_k]:
        cls = registry[name]
        results.append(TemplateMatch(
            template_name=name,
            description=cls.description,
            confidence=min(score, 1.0),
        ))

    return results


# ── LLM-ranked retrieval ──────────────────────────────────────────────────────

_TEMPLATE_RANK_SYSTEM = """\
You are a Manim template expert. Given a visual description and available templates,
determine which templates (if any) are useful as a starting point for the animation.

Not every scene needs a template — only suggest one if it genuinely matches.
Return JSON only.
"""

_TEMPLATE_RANK_PROMPT = """\
Visual intent: {visual_spec}
Narration context: {narration}

Beat actions:
{beat_actions}

Available templates:
{templates_list}

For each template, determine:
- relevant: true/false — does this template cover a significant part of the visual intent?
- confidence: 0.0-1.0 — how well does it match?
- suggested_params: dict of parameter values to fill (based on the visual intent)
- reasoning: one sentence explaining why/why not

Return JSON:
{{
  "matches": [
    {{
      "template_name": "...",
      "relevant": true,
      "confidence": 0.8,
      "suggested_params": {{}},
      "reasoning": "..."
    }}
  ],
  "use_template": true,
  "best_template": "template_name_or_null"
}}

Rules:
- Only mark "use_template": true if confidence >= 0.6 for at least one template
- If the visual intent is highly custom (not covered by any template), set "use_template": false
- suggested_params should use concrete values inferred from the visual description
"""


async def retrieve_templates(
    visual_spec: str,
    narration: str,
    beat_actions: list[str] | None = None,
    top_k: int = 3,
) -> list[TemplateMatch]:
    """LLM-ranked template retrieval. More accurate but costs tokens.

    First does keyword pre-filter, then asks LLM to rank and parameterize.
    Falls back to keyword-only if LLM fails.
    """
    # Step 1: Get all templates (library is small, show all to LLM)
    registry = _get_template_registry()
    all_templates = [
        {"name": name, "description": cls.description}
        for name, cls in registry.items()
    ]

    if not all_templates:
        return []

    templates_list = "\n".join(
        f"- {t['name']}: {t['description']}" for t in all_templates
    )

    beat_actions_text = "\n".join(
        f"  {i+1}. {action}" for i, action in enumerate(beat_actions or [])
    ) or "(no beats specified)"

    prompt = _TEMPLATE_RANK_PROMPT.format(
        visual_spec=visual_spec,
        narration=narration[:500],
        beat_actions=beat_actions_text,
        templates_list=templates_list,
    )

    try:
        llm = get_llm_provider()
        resp = await llm.complete(
            [LLMMessage(role="user", content=prompt)],
            system=_TEMPLATE_RANK_SYSTEM,
            max_tokens=2000,
            temperature=0,
        )

        content = resp.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        data = json.loads(content)
        matches = data.get("matches", [])

        results = []
        for m in matches:
            if not m.get("relevant", False):
                continue
            if m.get("confidence", 0) < 0.3:
                continue
            results.append(TemplateMatch(
                template_name=m["template_name"],
                description=next(
                    (t["description"] for t in all_templates if t["name"] == m["template_name"]),
                    "",
                ),
                confidence=m.get("confidence", 0.5),
                suggested_params=m.get("suggested_params", {}),
                reasoning=m.get("reasoning", ""),
            ))

        results.sort(key=lambda x: x.confidence, reverse=True)
        log.info(
            "template_rag.ranked",
            n_matches=len(results),
            best=results[0].template_name if results else None,
        )
        return results[:top_k]

    except (json.JSONDecodeError, KeyError, Exception) as e:
        log.warning("template_rag.llm_rank_failed", error=str(e))
        # Fallback to keyword-based
        return retrieve_templates_fast(
            visual_spec,
            beat_actions=beat_actions,
            top_k=top_k,
        )


def format_template_context(matches: list[TemplateMatch]) -> str:
    """Format template matches as context to inject into the codegen prompt.

    This is added to the LLM prompt so it knows about available templates
    and can use them as a starting point.
    """
    if not matches:
        return ""

    lines = [
        "\n═══ TEMPLATE SUGGESTIONS (use as starting point if confidence ≥ 0.6) ═══",
        "The following pre-built templates match your visual intent.",
        "You may use their structure as a starting point, adapting to the specific beats.",
        "If no template matches well (confidence < 0.6), write from scratch.\n",
    ]

    for m in matches:
        lines.append(f"Template: {m.template_name} (confidence: {m.confidence:.1f})")
        lines.append(f"  Description: {m.description}")
        if m.suggested_params:
            lines.append(f"  Suggested params: {json.dumps(m.suggested_params, ensure_ascii=False)}")
        if m.reasoning:
            lines.append(f"  Reasoning: {m.reasoning}")
        lines.append("")

    lines.append(
        "If using a template, ADAPT it — don't copy verbatim. Add the palette, "
        "beat markers, and style from the main prompt. Templates are starting points, "
        "not final outputs."
    )
    lines.append("═══\n")

    return "\n".join(lines)
