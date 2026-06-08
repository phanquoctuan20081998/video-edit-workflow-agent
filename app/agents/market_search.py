"""Stage 1 — Market search agent.

Scores topics on two independent axes:
  (a) trending — recency + engagement signals
  (b) manim_visualizable — can be animated with math/geometry/charts

Output: ranked list of TopicCandidate with reasoning + difficulty.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

import httpx
import structlog

from app.config import get_settings
from app.providers.base import LLMMessage
from app.providers.factory import get_llm_provider

log = structlog.get_logger()


@dataclass
class TopicCandidate:
    title: str
    source: str
    trending_score: float      # 0-10
    visualizable_score: float  # 0-10
    composite_score: float     # weighted sum
    difficulty: str            # "easy" | "medium" | "hard"
    approach: str              # suggested visual angle
    source_url: str = ""
    reasoning: str = ""


_SCORE_SYSTEM = """\
You are an expert at evaluating math/physics topics for educational video content.
Score each topic on two axes independently.
"""

_SCORE_PROMPT = """\
Score these topics for a math/physics explainer video channel.

Topics:
{topics_json}

For each topic, provide:
- trending_score (0-10): How trending/popular is this topic right now?
- visualizable_score (0-10): How well can this be visualized with Manim (math animations, geometry, charts)?
  10 = perfect (e.g., FFT, eigenvectors, neural networks)
  5  = ok but limited (e.g., historical events in math)
  0  = cannot animate meaningfully
- difficulty: "easy" | "medium" | "hard" (for implementation)
- approach: One sentence describing the best visual angle

Respond ONLY with JSON array:
[{{"title": "...", "trending_score": 8.5, "visualizable_score": 9, "difficulty": "medium", "approach": "..."}}]
"""


class MarketSearchAgent:
    def __init__(self, llm=None):
        self._llm = llm or get_llm_provider()
        self._cfg = get_settings()

    async def search(self, n_topics: int = 10) -> list[TopicCandidate]:
        """Fetch trending topics from multiple sources and rank them."""
        raw_topics: list[dict] = []

        # arXiv recent papers
        raw_topics.extend(await _fetch_arxiv_topics())

        # Reddit math/physics/ML posts
        raw_topics.extend(await _fetch_reddit_topics(self._cfg))

        # HN Ask/Show posts about math
        raw_topics.extend(await _fetch_hn_topics())

        if not raw_topics:
            log.warning("market_search.no_topics_found")
            return []

        # Deduplicate by title similarity
        raw_topics = _deduplicate(raw_topics)[:30]

        # Score with LLM
        scored = await self._score_topics(raw_topics)

        # Sort by composite score (equal weight both axes)
        ranked = sorted(scored, key=lambda t: t.composite_score, reverse=True)
        log.info("market_search.done", total=len(ranked))
        return ranked[:n_topics]

    async def _score_topics(self, raw_topics: list[dict]) -> list[TopicCandidate]:
        titles_json = json.dumps([{"title": t["title"], "source": t["source"]} for t in raw_topics])
        prompt = _SCORE_PROMPT.format(topics_json=titles_json)

        resp = await self._llm.complete(
            [LLMMessage(role="user", content=prompt)],
            system=_SCORE_SYSTEM,
            max_tokens=2000,
            temperature=0.3,
        )

        scored_raw = _parse_json_array(resp.content)
        scored_map = {s["title"].lower(): s for s in scored_raw}

        results = []
        for t in raw_topics:
            title_key = t["title"].lower()
            scores = scored_map.get(title_key, {})
            ts = float(scores.get("trending_score", 5.0))
            vs = float(scores.get("visualizable_score", 5.0))
            results.append(TopicCandidate(
                title=t["title"],
                source=t["source"],
                source_url=t.get("url", ""),
                trending_score=ts,
                visualizable_score=vs,
                composite_score=(ts + vs) / 2,
                difficulty=scores.get("difficulty", "medium"),
                approach=scores.get("approach", ""),
            ))

        return results


# ── Source fetchers ────────────────────────────────────────────────────────────

async def _fetch_arxiv_topics() -> list[dict]:
    topics = []
    try:
        import arxiv
        categories = ["math.CA", "math.NA", "cs.LG", "physics.class-ph"]
        client = arxiv.Client()
        for cat in categories[:2]:
            search = arxiv.Search(
                query=f"cat:{cat}",
                max_results=5,
                sort_by=arxiv.SortCriterion.SubmittedDate,
            )
            for paper in client.results(search):
                topics.append({
                    "title": paper.title,
                    "source": "arxiv",
                    "url": str(paper.entry_id),
                })
    except Exception as e:
        log.warning("market_search.arxiv_failed", error=str(e))
    return topics


async def _fetch_reddit_topics(cfg) -> list[dict]:
    topics = []
    if not cfg.reddit_client_id:
        log.info("market_search.reddit_skipped", reason="no credentials")
        return topics
    try:
        import praw
        reddit = praw.Reddit(
            client_id=cfg.reddit_client_id,
            client_secret=cfg.reddit_client_secret,
            user_agent=cfg.reddit_user_agent,
        )
        subreddits = ["math", "Physics", "MachineLearning", "learnmath"]
        for sub_name in subreddits[:2]:
            sub = reddit.subreddit(sub_name)
            for post in sub.hot(limit=5):
                if post.score > 50:
                    topics.append({
                        "title": post.title,
                        "source": f"reddit/r/{sub_name}",
                        "url": f"https://reddit.com{post.permalink}",
                    })
    except Exception as e:
        log.warning("market_search.reddit_failed", error=str(e))
    return topics


async def _fetch_hn_topics() -> list[dict]:
    topics = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://hacker-news.firebaseio.com/v0/topstories.json")
            story_ids = resp.json()[:30]
            for sid in story_ids[:10]:
                item_resp = await client.get(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json")
                item = item_resp.json()
                title = item.get("title", "")
                if any(kw in title.lower() for kw in ["math", "algorithm", "neural", "physics", "quantum", "geometry"]):
                    topics.append({
                        "title": title,
                        "source": "hackernews",
                        "url": item.get("url", f"https://news.ycombinator.com/item?id={sid}"),
                    })
    except Exception as e:
        log.warning("market_search.hn_failed", error=str(e))
    return topics


def _deduplicate(topics: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result = []
    for t in topics:
        key = t["title"].lower()[:50]
        if key not in seen:
            seen.add(key)
            result.append(t)
    return result


def _parse_json_array(text: str) -> list[dict]:
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return []
