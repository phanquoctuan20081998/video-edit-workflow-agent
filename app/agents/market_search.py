"""Stage 1 — Market search agent.

Scores topics on two independent axes:
  (a) trending   — anchored to real engagement signals (HN score, Reddit upvotes,
                   YouTube view-count rank, Google Trends rise value)
  (b) visualizable — can be animated with Manim (math/geometry/charts)

Sources: arXiv (all 4 categories), Reddit (PRAW auth OR public JSON fallback),
         HN (LLM relevance filter, not keyword), Google Trends, YouTube.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field

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
    trending_score: float       # 0-10, anchored to engagement_signal
    visualizable_score: float   # 0-10
    composite_score: float
    difficulty: str             # "easy" | "medium" | "hard"
    approach: str
    source_url: str = ""
    reasoning: str = ""
    engagement_signal: float = 0.0   # raw signal: HN pts, Reddit score, YT rank, Trends rise


# ── LLM prompts ────────────────────────────────────────────────────────────────

_HN_FILTER_SYSTEM = """\
You are filtering a list of Hacker News story titles to find ones about
math, physics, algorithms, CS theory, machine learning, or topics that can be
turned into an educational math/physics explainer video.
"""

_HN_FILTER_PROMPT = """\
From this list of Hacker News story titles, return ONLY those relevant to:
math, physics, algorithms, CS theory, machine learning, geometry, statistics,
linear algebra, signal processing, or any science/engineering concept that
could become a compelling explainer video with Manim animation.

Titles:
{titles_json}

Respond ONLY with a JSON array of the relevant titles (exact strings):
["title 1", "title 2", ...]

If none are relevant, return [].
"""

_SCORE_SYSTEM = """\
You are an expert at evaluating math/physics topics for an educational video channel.
Score each topic on two independent axes.
"""

_SCORE_PROMPT = """\
Score these topics for a math/physics explainer video channel.

Each topic includes an optional engagement_signal — a real number from live data:
  HN story: points (e.g. 850 = very hot)
  Reddit post: upvotes
  YouTube: rank among search results (1 = top, 10 = lower)
  Google Trends: rise percentage (e.g. 350 = +350% in 7 days)
  arXiv/other: 0 (no signal)

Use engagement_signal as the PRIMARY anchor for trending_score.
High signal → high trending_score. Do NOT rely solely on training knowledge.

Topics:
{topics_json}

For each topic provide:
- trending_score (0-10): based on engagement_signal + recency
- visualizable_score (0-10): how well Manim can animate this
  10 = perfect (FFT, eigenvectors, gradient descent, Fourier series)
  5  = limited (historical narrative, mostly text)
  0  = cannot animate meaningfully
- difficulty: "easy" | "medium" | "hard"
- approach: one sentence — best visual angle for Manim

Respond ONLY with JSON array:
[{{"title": "...", "trending_score": 8.5, "visualizable_score": 9, "difficulty": "medium", "approach": "..."}}]
"""


# ── Agent ──────────────────────────────────────────────────────────────────────

class MarketSearchAgent:
    def __init__(self, llm=None):
        self._llm = llm or get_llm_provider()
        self._cfg = get_settings()

    async def search(self, n_topics: int = 10) -> list[TopicCandidate]:
        raw_topics: list[dict] = []

        # Run all fetchers concurrently
        results = await asyncio.gather(
            _fetch_arxiv_topics(),
            _fetch_reddit_topics(self._cfg),
            _fetch_hn_topics(self._llm),
            _fetch_google_trends(self._cfg),
            _fetch_youtube_trends(self._cfg),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                log.warning("market_search.source_error", error=str(r))
            else:
                raw_topics.extend(r)

        if not raw_topics:
            log.warning("market_search.no_topics_found")
            return []

        raw_topics = _deduplicate(raw_topics)[:40]
        scored = await self._score_topics(raw_topics)
        ranked = sorted(scored, key=lambda t: t.composite_score, reverse=True)
        log.info("market_search.done", total=len(ranked))
        return ranked[:n_topics]

    async def _score_topics(self, raw_topics: list[dict]) -> list[TopicCandidate]:
        payload = [
            {
                "title": t["title"],
                "source": t["source"],
                "engagement_signal": t.get("engagement_signal", 0),
            }
            for t in raw_topics
        ]
        prompt = _SCORE_PROMPT.format(topics_json=json.dumps(payload, ensure_ascii=False))

        resp = await self._llm.complete(
            [LLMMessage(role="user", content=prompt)],
            system=_SCORE_SYSTEM,
            max_tokens=2500,
            temperature=0.2,
        )

        scored_raw = _parse_json_array(resp.content)
        scored_map = {s["title"].lower(): s for s in scored_raw}

        results = []
        for t in raw_topics:
            scores = scored_map.get(t["title"].lower(), {})
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
                engagement_signal=t.get("engagement_signal", 0.0),
            ))
        return results


# ── Source fetchers ────────────────────────────────────────────────────────────

async def _fetch_arxiv_topics() -> list[dict]:
    topics = []
    try:
        import arxiv
        # Fix: all 4 categories, not just [:2]
        categories = ["math.CA", "math.NA", "cs.LG", "physics.class-ph"]
        client = arxiv.Client()
        for cat in categories:
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
                    "engagement_signal": 0,   # no engagement metric for arXiv
                })
        log.info("market_search.arxiv_done", count=len(topics))
    except Exception as e:
        log.warning("market_search.arxiv_failed", error=str(e))
    return topics


async def _fetch_reddit_topics(cfg) -> list[dict]:
    """Auth mode (PRAW) when credentials present; public JSON fallback otherwise."""
    if cfg.reddit_client_id:
        return await _reddit_praw(cfg)
    return await _reddit_public_json(cfg)


async def _reddit_praw(cfg) -> list[dict]:
    topics = []
    try:
        import praw
        reddit = praw.Reddit(
            client_id=cfg.reddit_client_id,
            client_secret=cfg.reddit_client_secret,
            user_agent=cfg.reddit_user_agent,
        )
        subreddits = ["math", "Physics", "MachineLearning", "learnmath"]
        for sub_name in subreddits:
            sub = reddit.subreddit(sub_name)
            for post in sub.hot(limit=10):
                if post.score > 50:
                    topics.append({
                        "title": post.title,
                        "source": f"reddit/r/{sub_name}",
                        "url": f"https://reddit.com{post.permalink}",
                        "engagement_signal": float(post.score),
                    })
        log.info("market_search.reddit_praw_done", count=len(topics))
    except Exception as e:
        log.warning("market_search.reddit_praw_failed", error=str(e))
        # Fall through to public JSON
        topics = await _reddit_public_json(cfg)
    return topics


async def _reddit_public_json(cfg) -> list[dict]:
    """No-auth fallback — Reddit public JSON endpoint, no credentials required."""
    topics = []
    subreddits = ["math", "Physics", "MachineLearning", "learnmath"]
    headers = {"User-Agent": cfg.reddit_user_agent}
    try:
        async with httpx.AsyncClient(timeout=15, headers=headers, follow_redirects=True) as client:
            for sub_name in subreddits:
                try:
                    resp = await client.get(
                        f"https://www.reddit.com/r/{sub_name}/hot.json",
                        params={"limit": 25},
                    )
                    resp.raise_for_status()
                    posts = resp.json().get("data", {}).get("children", [])
                    for post in posts:
                        d = post.get("data", {})
                        score = d.get("score", 0)
                        if score > 50:
                            topics.append({
                                "title": d.get("title", "").strip(),
                                "source": f"reddit/r/{sub_name}",
                                "url": f"https://reddit.com{d.get('permalink', '')}",
                                "engagement_signal": float(score),
                            })
                except Exception as e:
                    log.warning("market_search.reddit_public_sub_failed", sub=sub_name, error=str(e))
        log.info("market_search.reddit_public_done", count=len(topics))
    except Exception as e:
        log.warning("market_search.reddit_public_failed", error=str(e))
    return topics


async def _fetch_hn_topics(llm) -> list[dict]:
    """Fetch top 50 HN stories, use LLM to classify relevance — no keyword filter."""
    raw: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://hacker-news.firebaseio.com/v0/topstories.json")
            story_ids = resp.json()[:50]

            # Fetch all stories concurrently
            async def fetch_one(sid: int) -> dict | None:
                try:
                    r = await client.get(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json")
                    item = r.json()
                    if item and item.get("type") == "story" and item.get("title"):
                        return {
                            "id": sid,
                            "title": item["title"].strip(),
                            "url": item.get("url", f"https://news.ycombinator.com/item?id={sid}"),
                            "score": item.get("score", 0),
                        }
                except Exception:
                    pass
                return None

            items = await asyncio.gather(*[fetch_one(sid) for sid in story_ids])
            raw = [i for i in items if i is not None]

    except Exception as e:
        log.warning("market_search.hn_fetch_failed", error=str(e))
        return []

    if not raw:
        return []

    # LLM relevance filter — one batch call, no per-title keyword matching
    titles_json = json.dumps([r["title"] for r in raw])
    try:
        resp = await llm.complete(
            [LLMMessage(role="user", content=_HN_FILTER_PROMPT.format(titles_json=titles_json))],
            system=_HN_FILTER_SYSTEM,
            max_tokens=1024,
            temperature=0.1,
        )
        relevant_titles: set[str] = set(_parse_json_array_strings(resp.content))
    except Exception as e:
        log.warning("market_search.hn_llm_filter_failed", error=str(e))
        relevant_titles = set()

    score_map = {r["title"]: r["score"] for r in raw}
    url_map = {r["title"]: r["url"] for r in raw}

    topics = []
    for title in relevant_titles:
        if title:
            topics.append({
                "title": title,
                "source": "hackernews",
                "url": url_map.get(title, "https://news.ycombinator.com"),
                "engagement_signal": float(score_map.get(title, 0)),
            })

    log.info("market_search.hn_done", fetched=len(raw), relevant=len(topics))
    return topics


async def _fetch_google_trends(cfg) -> list[dict]:
    """Rising queries from Google Trends — rise value passed as engagement_signal."""
    topics = []
    try:
        from pytrends.request import TrendReq

        seed_keywords = [k.strip() for k in cfg.google_trends_keywords.split(",")]
        geo = cfg.google_trends_geo

        def _sync_fetch() -> list[dict]:
            pt = TrendReq(hl="en-US", tz=0, timeout=(10, 30))
            results = []
            for i in range(0, len(seed_keywords), 5):
                batch = seed_keywords[i:i + 5]
                try:
                    pt.build_payload(batch, cat=0, timeframe="now 7-d", geo=geo)
                    related = pt.related_queries()
                    for kw in batch:
                        rising = related.get(kw, {}).get("rising")
                        if rising is not None and not rising.empty:
                            for _, row in rising.head(5).iterrows():
                                query = str(row.get("query", "")).strip()
                                rise_val = float(row.get("value", 0))
                                if query:
                                    results.append({
                                        "title": query,
                                        "source": "google_trends",
                                        "url": f"https://trends.google.com/trends/explore?q={query}&geo={geo}",
                                        "engagement_signal": rise_val,   # e.g. 350 = +350%
                                    })
                except Exception as e:
                    log.warning("market_search.google_trends_batch_failed", batch=batch, error=str(e))
            return results

        topics = await asyncio.get_event_loop().run_in_executor(None, _sync_fetch)
        log.info("market_search.google_trends_done", count=len(topics))
    except ImportError:
        log.warning("market_search.google_trends_skipped", reason="pytrends not installed")
    except Exception as e:
        log.warning("market_search.google_trends_failed", error=str(e))
    return topics


async def _fetch_youtube_trends(cfg) -> list[dict]:
    """Trending math/science videos via YouTube Data API v3.
    engagement_signal = inverted rank (top result = highest signal).
    """
    topics = []
    if not cfg.youtube_api_key:
        log.info("market_search.youtube_skipped", reason="no YOUTUBE_API_KEY")
        return topics
    try:
        from googleapiclient.discovery import build

        search_terms = [k.strip() for k in cfg.youtube_search_keywords.split(",")]

        def _sync_fetch() -> list[dict]:
            youtube = build("youtube", "v3", developerKey=cfg.youtube_api_key)
            results = []
            for term in search_terms:
                try:
                    response = youtube.search().list(
                        q=term,
                        part="snippet",
                        type="video",
                        order="viewCount",
                        publishedAfter="2024-01-01T00:00:00Z",
                        videoCategoryId="28",
                        maxResults=cfg.youtube_max_results,
                        relevanceLanguage="en",
                    ).execute()
                    items = response.get("items", [])
                    n = len(items)
                    for rank, item in enumerate(items):
                        snippet = item.get("snippet", {})
                        title = snippet.get("title", "").strip()
                        video_id = item.get("id", {}).get("videoId", "")
                        if title:
                            # Top result gets highest signal score
                            signal = round((n - rank) / n * 10, 1)
                            results.append({
                                "title": title,
                                "source": "youtube",
                                "url": f"https://www.youtube.com/watch?v={video_id}",
                                "engagement_signal": signal,
                            })
                except Exception as e:
                    log.warning("market_search.youtube_search_failed", term=term, error=str(e))
            return results

        topics = await asyncio.get_event_loop().run_in_executor(None, _sync_fetch)
        log.info("market_search.youtube_done", count=len(topics))
    except ImportError:
        log.warning("market_search.youtube_skipped", reason="google-api-python-client not installed")
    except Exception as e:
        log.warning("market_search.youtube_failed", error=str(e))
    return topics


# ── Utilities ──────────────────────────────────────────────────────────────────

def _deduplicate(topics: list[dict]) -> list[dict]:
    """Deduplicate by lowercased title prefix. Keep highest engagement_signal on collision."""
    best: dict[str, dict] = {}
    for t in topics:
        key = t["title"].lower()[:60]
        if key not in best or t.get("engagement_signal", 0) > best[key].get("engagement_signal", 0):
            best[key] = t
    return list(best.values())


def _parse_json_array(text: str) -> list[dict]:
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return []


def _parse_json_array_strings(text: str) -> list[str]:
    """Parse a JSON array of strings from LLM output."""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        result = json.loads(match.group())
        return [s for s in result if isinstance(s, str)]
    except json.JSONDecodeError:
        return []
