"""Main Streamlit application — multi-page navigation."""

import streamlit as st

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

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🎬 Video Agent")
    st.divider()

    proj = st.session_state.get("current_project")
    if proj:
        st.markdown(f"""
        <div class="project-badge">
            <div class="label">Active Project</div>
            <div class="value">{proj.get("topic", "—")[:40]}</div>
            <div style="margin-top:4px; color:#6b7280; font-size:11px;">
                {proj.get("language","").upper()} &nbsp;·&nbsp; {proj.get("status","—")}
            </div>
        </div>
        """, unsafe_allow_html=True)

    page = st.radio(
        "Navigation",
        [
            "🔄  Workflow",
            "📁  Projects",
            "🔍  Topic Search",
            "📝  Script",
            "🎬  Scene QA",
        ],
        label_visibility="collapsed",
    )

    st.divider()
    st.caption("Video Agent · v0.1")

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
