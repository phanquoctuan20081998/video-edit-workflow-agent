"""Stage 1 — Topic review: market search with interest prompt and run history.

Background-task pattern:
  - Search runs in a daemon thread via ThreadPoolExecutor.
  - Results land in a @st.cache_resource dict (survives page navigation).
  - A st.fragment(run_every=2) polls the store every 2s without a full page
    rerun, eliminating the spinner flicker of the old sleep(1)+rerun pattern.
"""

from __future__ import annotations

import concurrent.futures
import time
import uuid
from datetime import datetime

import streamlit as st

from webui.storage import load_topic_searches, save_topic_search, save_project


_LANG_OPTIONS = ["en", "vi", "ja", "zh", "ko", "fr", "de", "es"]


# ── Background task store (survives navigation within the session) ────────────

@st.cache_resource
def _task_store() -> dict:
    """Module-level singleton: {run_key: {"status": running|done|error, ...}}"""
    return {}


@st.cache_resource
def _executor() -> concurrent.futures.ThreadPoolExecutor:
    return concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="market_search")


@st.fragment(run_every=2)
def _running_poll(run_key: str) -> None:
    store = _task_store()
    task = store.get(run_key, {})
    if task.get("status") == "running":
        elapsed = int(time.monotonic() - task.get("started_at", time.monotonic()))
        st.info(f"🔍 Market search running… ({elapsed}s elapsed)")
        for msg in task.get("log", []):
            st.caption(f"▸ {msg}")
    else:
        st.rerun()


def _start_search(run_key: str, prompt: str, n_topics: int) -> None:
    store = _task_store()
    store[run_key] = {"status": "running", "prompt": prompt, "log": [], "started_at": time.monotonic()}

    def _worker():
        try:
            import asyncio
            from app.agents.market_search import MarketSearchAgent
            store[run_key]["log"].append("Initializing market search agent…")
            agent = MarketSearchAgent()
            store[run_key]["log"].append("Querying arXiv, Reddit, HackerNews, YouTube Trends…")
            candidates = asyncio.run(agent.search(
                n_topics=n_topics,
                interest_prompt=prompt or None,
            ))
            store[run_key]["log"].append(f"Scoring {len(candidates)} candidates by trending + visualizability…")
            result = [
                {
                    "title":     c.title,
                    "source":    c.source,
                    "trending":  c.trending_score,
                    "visual":    c.visualizable_score,
                    "composite": c.composite_score,
                    "difficulty": c.difficulty,
                    "approach":  c.approach,
                    "url":       c.source_url,
                }
                for c in candidates
            ]
            store[run_key] = {"status": "done", "prompt": prompt, "candidates": result}
        except Exception as e:
            store[run_key] = {"status": "error", "prompt": prompt, "error": str(e)}

    _executor().submit(_worker)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_dt(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso[:16]


def _render_candidates(candidates: list[dict], key_prefix: str) -> None:
    cols = st.columns([4, 1, 1, 1, 1])
    cols[0].caption("Topic")
    cols[1].caption("Trend")
    cols[2].caption("Visual")
    cols[3].caption("Difficulty")

    selected_idx = st.session_state.get("selected_topic_idx")
    for i, c in enumerate(candidates):
        is_sel = (i == selected_idx)
        row = st.columns([4, 1, 1, 1, 1])
        label = f"{'✓ ' if is_sel else ''}{c['title']}"
        row[0].markdown(f"**{label}**" if is_sel else label)
        row[1].markdown(f"`{c['trending']:.1f}`")
        row[2].markdown(f"`{c['visual']:.1f}`")
        row[3].caption(c["difficulty"])
        if row[4].button("Select", key=f"{key_prefix}_sel_{i}"):
            st.session_state["selected_topic_idx"] = i
            st.session_state["topic_candidates"] = candidates
            st.rerun()


# ── Main render ───────────────────────────────────────────────────────────────

def render() -> None:
    st.title("Stage 1 — Topic Search")

    store   = _task_store()
    run_key = st.session_state.get("search_run_key")

    # ── Poll running task ─────────────────────────────────────────────────────
    if run_key and run_key in store:
        task = store[run_key]

        if task["status"] == "running":
            _running_poll(run_key)
            return

        elif task["status"] == "done":
            candidates = task["candidates"]
            proj_now = st.session_state.get("current_project") or {}
            pid = proj_now.get("project_id", "")
            st.session_state["topic_candidates"] = candidates
            st.session_state["selected_topic_idx"] = None
            save_topic_search(pid, task.get("prompt", ""), candidates)
            if pid:
                save_project(pid, proj_now.get("topic", ""), proj_now.get("language", "en"), "searched")
                if "current_project" in st.session_state:
                    st.session_state["current_project"]["status"] = "searched"
            del store[run_key]
            st.session_state.pop("search_run_key", None)
            st.rerun()
            return

        elif task["status"] == "error":
            st.error(f"Search failed: {task['error']}")
            del store[run_key]
            st.session_state.pop("search_run_key", None)

    # ── Interest prompt ───────────────────────────────────────────────────────
    st.subheader("What topics are you interested in? *")
    st.caption("Required — the search focuses on topics related to what you type here.")

    interest_prompt = st.text_area(
        "Interest prompt",
        value=st.session_state.get("interest_prompt", ""),
        placeholder="e.g. linear algebra, neural networks, quantum computing, Fourier transforms",
        height=90,
        label_visibility="collapsed",
    )
    st.session_state["interest_prompt"] = interest_prompt

    col1, col2, col3 = st.columns([1, 1, 3])
    n_topics = col1.number_input("Results", min_value=3, max_value=20, value=10)
    language = col2.selectbox("Language", _LANG_OPTIONS, index=0, key="search_language")

    if col3.button("🔍 Run Market Search", type="primary"):
        if not interest_prompt.strip():
            st.error("Enter at least one topic of interest before running the search.")
        else:
            key = str(uuid.uuid4())[:8]
            st.session_state["search_run_key"] = key
            proj = st.session_state.get("current_project") or {}
            pid  = proj.get("project_id", "")
            if pid:
                save_project(pid, proj.get("topic", ""), proj.get("language", "en"), "searching")
                st.session_state["current_project"]["status"] = "searching"
            _start_search(key, interest_prompt, int(n_topics))
            st.rerun()

    st.divider()

    # ── History ───────────────────────────────────────────────────────────────
    current_pid = (st.session_state.get("current_project") or {}).get("project_id")
    history = load_topic_searches(project_id=current_pid)
    if history:
        with st.expander(f"📋 Search History ({len(history)} runs)", expanded=False):
            for run in history:
                h_cols = st.columns([2, 3, 1, 1])
                h_cols[0].caption(_fmt_dt(run.get("timestamp", "")))
                prompt_preview = (run.get("prompt") or "(no prompt)")[:60]
                h_cols[1].caption(prompt_preview)
                h_cols[2].caption(f"{len(run.get('candidates', []))} topics")
                if h_cols[3].button("Load", key=f"hist_{run['run_id']}"):
                    st.session_state["topic_candidates"] = run["candidates"]
                    st.session_state["selected_topic_idx"] = None
                    st.rerun()

    # ── Results ───────────────────────────────────────────────────────────────
    candidates = st.session_state.get("topic_candidates", [])
    if not candidates:
        st.info("Run a market search above, or load a previous run from history.")
        return

    st.subheader(f"Results — {len(candidates)} topics")
    _render_candidates(candidates, key_prefix="curr")

    # ── Topic approval ────────────────────────────────────────────────────────
    selected_idx = st.session_state.get("selected_topic_idx")
    if selected_idx is not None:
        c = candidates[selected_idx]
        st.divider()
        st.subheader(f"Selected: {c['title']}")
        st.markdown(f"**Approach:** {c['approach']}")
        if c.get("url"):
            st.markdown(f"**Source:** [{c['source']}]({c['url']})")
        else:
            st.markdown(f"**Source:** {c['source']}")
        st.markdown(
            f"Composite score: **{c['composite']:.1f}**/10 &nbsp;|&nbsp; Difficulty: **{c['difficulty']}**",
            unsafe_allow_html=True,
        )

        custom     = st.text_input("Or enter a custom topic:", "", key="custom_topic")
        final_topic = custom.strip() if custom.strip() else c["title"]
        final_lang  = st.selectbox(
            "Language for this project", _LANG_OPTIONS,
            index=_LANG_OPTIONS.index(language) if language in _LANG_OPTIONS else 0,
            key="final_lang",
        )

        if st.button("Approve → Start Scripting", type="primary"):
            pid = (st.session_state.get("current_project") or {}).get("project_id") or str(uuid.uuid4())
            save_project(pid, final_topic, final_lang, "searched")
            st.session_state["current_project"] = {
                "project_id": pid,
                "topic": final_topic,
                "language": final_lang,
                "status": "searched",
            }
            st.session_state["approved_topic"] = final_topic
            st.session_state["language"] = final_lang
            st.session_state["pending_stage_nav"] = "📝  Script"
            st.rerun()
