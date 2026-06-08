"""HITL Page 1 — Topic review and approval."""

from __future__ import annotations

import asyncio
import json

import streamlit as st


def render():
    st.title("Stage 1 — Topic Review")
    st.markdown("Market search results. Select a topic to proceed to scripting.")

    # ── Run market search ──────────────────────────────────────────────────────
    if st.button("Run Market Search", type="primary"):
        with st.spinner("Searching trending math/physics topics..."):
            from app.agents.market_search import MarketSearchAgent
            agent = MarketSearchAgent()
            candidates = asyncio.run(agent.search(n_topics=10))
            st.session_state["topic_candidates"] = [
                {
                    "title": c.title,
                    "source": c.source,
                    "trending": c.trending_score,
                    "visual": c.visualizable_score,
                    "composite": c.composite_score,
                    "difficulty": c.difficulty,
                    "approach": c.approach,
                    "url": c.source_url,
                }
                for c in candidates
            ]

    candidates = st.session_state.get("topic_candidates", [])
    if not candidates:
        st.info("Click 'Run Market Search' to fetch trending topics.")
        return

    st.markdown(f"**{len(candidates)} topics found.** Click a row to select.")

    selected_idx = st.session_state.get("selected_topic_idx", None)

    for i, c in enumerate(candidates):
        cols = st.columns([4, 1, 1, 1, 2])
        is_selected = (i == selected_idx)
        cols[0].markdown(f"{'**' if is_selected else ''}{'✓ ' if is_selected else ''}{c['title']}{'**' if is_selected else ''}")
        cols[1].metric("Trend", f"{c['trending']:.1f}")
        cols[2].metric("Visual", f"{c['visual']:.1f}")
        cols[3].caption(c["difficulty"])
        if cols[4].button("Select", key=f"sel_{i}"):
            st.session_state["selected_topic_idx"] = i
            st.rerun()

    if selected_idx is not None:
        c = candidates[selected_idx]
        st.divider()
        st.subheader(f"Selected: {c['title']}")
        st.markdown(f"**Approach:** {c['approach']}")
        st.markdown(f"**Source:** [{c['source']}]({c['url']})" if c["url"] else f"**Source:** {c['source']}")
        st.markdown(f"Composite score: **{c['composite']:.1f}**/10 | Difficulty: **{c['difficulty']}**")

        custom = st.text_input("Or enter custom topic:", "")
        final_topic = custom.strip() if custom.strip() else c["title"]
        language = st.selectbox("Language", ["vi", "en", "ja"], index=0)

        if st.button("Approve → Start Scripting", type="primary"):
            st.session_state["approved_topic"] = final_topic
            st.session_state["language"] = language
            st.success(f"Topic approved: **{final_topic}**. Go to Script Review.")
