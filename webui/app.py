"""Streamlit HITL UI — topic → script → scene QA → final render."""

import sys
from pathlib import Path

import streamlit as st

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT in sys.path:
    sys.path.remove(ROOT)
sys.path.insert(0, ROOT)

st.set_page_config(
    page_title="Video Agent",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    /* Hide default Streamlit page nav */
    [data-testid="stSidebarNav"] { display: none; }

    /* Sidebar project badge */
    .project-badge {
        background: #1e2130;
        border: 1px solid #374151;
        border-radius: 8px;
        padding: 10px 14px;
        margin-bottom: 12px;
        font-size: 13px;
    }
    .project-badge .label {
        color: #6b7280;
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .project-badge .value {
        color: #e5e7eb;
        font-weight: 600;
        margin-top: 2px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .status-chip {
        display: inline-block;
        font-size: 11px;
        font-weight: 600;
        padding: 2px 8px;
        border-radius: 10px;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }
</style>
""", unsafe_allow_html=True)

from webui.state import hydrate_session_state

hydrate_session_state(st.session_state)

PAGES = {
    "🔄  Workflow":        "webui/pages/workflow.py",
    "📁  Projects":        "webui/pages/projects.py",
    "Topic Review":        "webui/pages/topic_review.py",
    "Script Review":       "webui/pages/script_review.py",
    "Scene QA":            "webui/pages/scene_review.py",
    "Voiceover + Render":  "webui/pages/final_render.py",
}

st.sidebar.title("🎬 Video Agent")

current_spec = st.session_state.get("approved_spec") or st.session_state.get("draft_spec")
if current_spec:
    from app.models.video_spec import VideoSpec
    from webui.state import save_spec

    spec = VideoSpec.model_validate(current_spec)
    st.sidebar.markdown(f"""
    <div class="project-badge">
        <div class="label">Active Project</div>
        <div class="value">{spec.topic[:40]}</div>
        <div style="margin-top:4px; color:#6b7280; font-size:11px;">
            {spec.language.upper()} &nbsp;·&nbsp; {spec.status}
        </div>
    </div>
    """, unsafe_allow_html=True)
    if st.sidebar.button("Save current project", key="save_current_project"):
        save_spec(spec)
        st.sidebar.success("Saved")

st.sidebar.markdown("---")

pending_stage = st.session_state.pop("pending_stage_nav", None)
if pending_stage in PAGES:
    st.session_state["stage_nav"] = pending_stage

page = st.sidebar.radio("Stage", list(PAGES.keys()), key="stage_nav")

st.sidebar.divider()
st.sidebar.caption("Video Agent · v0.1")

# ── Route ─────────────────────────────────────────────────────────────────────

if "Workflow" in page:
    from webui.pages import workflow
    workflow.render()
elif "Projects" in page:
    from webui.pages import projects
    projects.render()
elif "Topic" in page:
    from webui.pages import topic_review
    topic_review.render()
elif "Script" in page:
    from webui.pages import script_review
    script_review.render()
elif "Scene" in page:
    from webui.pages import scene_review
    scene_review.render()
elif page == "Voiceover + Render":
    from webui.pages import final_render
    final_render.render()
