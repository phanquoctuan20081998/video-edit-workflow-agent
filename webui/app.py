"""Streamlit HITL UI — topic → script → scene QA → final render."""

import sys
from pathlib import Path

import streamlit as st

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT in sys.path:
    sys.path.remove(ROOT)
sys.path.insert(0, ROOT)

st.set_page_config(
    page_title="Video Agent — Review",
    page_icon="🎬",
    layout="wide",
)

from webui.state import hydrate_session_state

hydrate_session_state(st.session_state)

PAGES = {
    "Topic Review": "webui/pages/topic_review.py",
    "Script Review": "webui/pages/script_review.py",
    "Scene QA": "webui/pages/scene_review.py",
    "Voiceover + Render": "webui/pages/final_render.py",
}

st.sidebar.title("Video Agent")
current_spec = st.session_state.get("approved_spec") or st.session_state.get("draft_spec")
if current_spec:
    from app.models.video_spec import VideoSpec
    from webui.state import save_spec

    spec = VideoSpec.model_validate(current_spec)
    st.sidebar.caption(f"Project: {spec.project_id}")
    if st.sidebar.button("Save current project", key="save_current_project"):
        save_spec(spec)
        st.sidebar.success("Saved")

st.sidebar.markdown("---")

pending_stage = st.session_state.pop("pending_stage_nav", None)
if pending_stage in PAGES:
    st.session_state["stage_nav"] = pending_stage

page = st.sidebar.radio("Stage", list(PAGES.keys()), key="stage_nav")

if page == "Topic Review":
    from webui.pages import topic_review
    topic_review.render()
elif page == "Script Review":
    from webui.pages import script_review
    script_review.render()
elif page == "Scene QA":
    from webui.pages import scene_review
    scene_review.render()
elif page == "Voiceover + Render":
    from webui.pages import final_render
    final_render.render()
