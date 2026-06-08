"""Streamlit HITL UI — 3 review pages: topic → script → scene QA."""

import streamlit as st

st.set_page_config(
    page_title="Video Agent — Review",
    page_icon="🎬",
    layout="wide",
)

PAGES = {
    "Topic Review": "webui/pages/topic_review.py",
    "Script Review": "webui/pages/script_review.py",
    "Scene QA": "webui/pages/scene_review.py",
}

st.sidebar.title("Video Agent")
st.sidebar.markdown("---")

page = st.sidebar.radio("Stage", list(PAGES.keys()))

if page == "Topic Review":
    from webui.pages import topic_review
    topic_review.render()
elif page == "Script Review":
    from webui.pages import script_review
    script_review.render()
elif page == "Scene QA":
    from webui.pages import scene_review
    scene_review.render()
