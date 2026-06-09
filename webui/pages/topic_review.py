"""Stage 1 — Topic review: market search with interest prompt and run history."""

from __future__ import annotations

import asyncio
from datetime import datetime

import streamlit as st

from webui.storage import load_topic_searches, save_topic_search, save_project


_LANG_OPTIONS = ["en", "vi", "ja", "zh", "ko", "fr", "de", "es"]


def _fmt_dt(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso[:16]


def _run_search(prompt: str, n_topics: int) -> list[dict]:
    from app.agents.market_search import MarketSearchAgent
    agent = MarketSearchAgent()
    candidates = asyncio.run(agent.search(n_topics=n_topics, interest_prompt=prompt or None))
    return [
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


def _render_candidates(candidates: list[dict], key_prefix: str) -> None:
    selected_idx = st.session_state.get("selected_topic_idx")

    header = st.columns([4, 1, 1, 1, 1])
    header[0].caption("Topic")
    header[1].caption("Trend")
    header[2].caption("Visual")
    header[3].caption("Difficulty")

    for i, c in enumerate(candidates):
        is_sel = (i == selected_idx)
        cols = st.columns([4, 1, 1, 1, 1])
        label = f"{'✓ ' if is_sel else ''}{c['title']}"
        cols[0].markdown(f"**{label}**" if is_sel else label)
        cols[1].markdown(f"`{c['trending']:.1f}`")
        cols[2].markdown(f"`{c['visual']:.1f}`")
        cols[3].caption(c["difficulty"])
        if cols[4].button("Select", key=f"{key_prefix}_sel_{i}"):
            st.session_state["selected_topic_idx"] = i
            st.session_state["topic_candidates"] = candidates
            st.rerun()


def render() -> None:
    st.title("Stage 1 — Topic Search")

    # ── Interest prompt ───────────────────────────────────────────────────────
    st.subheader("Topic Interests")
    st.caption(
        "Describe what topics you're interested in. The market search will bias results toward these areas."
    )
    interest_prompt = st.text_area(
        "Interest prompt",
        value=st.session_state.get("interest_prompt", ""),
        placeholder="e.g. I want topics about linear algebra, neural network theory, or quantum computing.",
        height=90,
        label_visibility="collapsed",
    )
    st.session_state["interest_prompt"] = interest_prompt

    col1, col2, col3 = st.columns([1, 1, 3])
    n_topics = col1.number_input("Results", min_value=3, max_value=20, value=10)
    language = col2.selectbox("Language", _LANG_OPTIONS, index=0, key="search_language")

    if col3.button("🔍 Run Market Search", type="primary"):
        with st.spinner("Searching trending math/physics topics..."):
            try:
                candidates = _run_search(interest_prompt, int(n_topics))
                st.session_state["topic_candidates"] = candidates
                st.session_state["selected_topic_idx"] = None
                save_topic_search(interest_prompt, candidates)
            except Exception as e:
                st.error(f"Search failed: {e}")
                return

    st.divider()

    # ── History ───────────────────────────────────────────────────────────────
    history = load_topic_searches()
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

    # ── Current results ───────────────────────────────────────────────────────
    candidates = st.session_state.get("topic_candidates", [])
    if not candidates:
        st.info("Run a market search above, or load a previous run from history.")
        return

    st.subheader(f"Results — {len(candidates)} topics")
    _render_candidates(candidates, key_prefix="curr")

    # ── Topic selection & approval ────────────────────────────────────────────
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

        custom = st.text_input("Or enter a custom topic:", "", key="custom_topic")
        final_topic = custom.strip() if custom.strip() else c["title"]
        final_lang  = st.selectbox("Language for this project", _LANG_OPTIONS,
                                   index=_LANG_OPTIONS.index(language), key="final_lang")

        if st.button("Approve → Start Scripting", type="primary"):
            import uuid
            pid = st.session_state.get("current_project", {}).get("project_id") or str(uuid.uuid4())
            save_project(pid, final_topic, final_lang, "searched")
            st.session_state["current_project"] = {
                "project_id": pid,
                "topic": final_topic,
                "language": final_lang,
                "status": "searched",
            }
            st.session_state["approved_topic"] = final_topic
            st.session_state["language"] = final_lang
            st.success(f"Topic approved: **{final_topic}**. Go to **Script** to generate the VideoSpec.")
