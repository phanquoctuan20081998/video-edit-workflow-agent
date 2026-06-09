"""Streamlit HITL UI — topic → script → scene QA → final render."""

import sys
import uuid
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
    [data-testid="stSidebarNav"] { display: none; }

    .proj-header {
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #6b7280;
        margin-bottom: 4px;
    }
    .proj-status {
        display: inline-block;
        font-size: 10px;
        font-weight: 700;
        padding: 1px 7px;
        border-radius: 8px;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-top: 4px;
    }
    .status-searched   { background:#1e3a5f; color:#93c5fd; }
    .status-scripted   { background:#3b1f6e; color:#c4b5fd; }
    .status-approved   { background:#0e3a2e; color:#6ee7b7; }
    .status-animated   { background:#451a03; color:#fcd34d; }
    .status-voiced     { background:#1a2e1a; color:#86efac; }
    .status-composited { background:#1e1a45; color:#a5b4fc; }
    .status-rendered   { background:#052e16; color:#4ade80; }
</style>
""", unsafe_allow_html=True)

from webui.state import hydrate_session_state
from webui.storage import load_projects, save_project, load_project_spec

hydrate_session_state(st.session_state)

_PROJECT_KEYS = [
    "draft_spec", "approved_spec", "qa_approved_spec",
    "topic_candidates", "selected_topic_idx",
    "active_script_id", "interest_prompt",
    "search_run_key", "script_run_key",
    "scene_run_key", "render_run_key",
    "approved_topic", "language",
]

def _reset_project_session(ss) -> None:
    for k in _PROJECT_KEYS:
        ss.pop(k, None)

_LANG_OPTIONS = ["en", "vi", "ja", "zh", "ko", "fr", "de", "es"]

_STATUS_CSS = {
    "searched": "status-searched", "scripted": "status-scripted",
    "approved": "status-approved", "animated": "status-animated",
    "voiced": "status-voiced", "composited": "status-composited",
    "rendered": "status-rendered",
}

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🎬 Video Agent")
    st.divider()

    # ── Project selector ──────────────────────────────────────────────────────
    st.markdown('<div class="proj-header">Project</div>', unsafe_allow_html=True)

    projects = load_projects()
    current_pid = (st.session_state.get("current_project") or {}).get("project_id", "")

    proj_labels = ["＋ New project"] + [p["topic"][:32] for p in projects]
    proj_ids    = [None]              + [p["project_id"]  for p in projects]

    current_idx = next(
        (i + 1 for i, p in enumerate(projects) if p["project_id"] == current_pid),
        0,
    )

    selected_idx = st.selectbox(
        "project_select",
        range(len(proj_labels)),
        format_func=lambda i: proj_labels[i],
        index=current_idx,
        label_visibility="collapsed",
        key="project_selector",
    )

    selected_pid = proj_ids[selected_idx]

    # ── New project form ──────────────────────────────────────────────────────
    if selected_pid is None:
        with st.form("new_project_form", clear_on_submit=True):
            new_topic = st.text_input("Project name", placeholder="e.g. Fast Fourier Transform")
            if st.form_submit_button("Create", type="primary") and new_topic.strip():
                pid = str(uuid.uuid4())
                save_project(pid, new_topic.strip(), "en", "searched")
                _reset_project_session(st.session_state)
                st.session_state["current_project"] = {
                    "project_id": pid,
                    "topic": new_topic.strip(),
                    "language": "en",
                    "status": "searched",
                }
                st.session_state["approved_topic"] = new_topic.strip()
                st.session_state["language"] = "en"
                st.rerun()

    # ── Load project when selection changes ───────────────────────────────────
    elif selected_pid != current_pid:
        proj_data = next(p for p in projects if p["project_id"] == selected_pid)
        spec = load_project_spec(selected_pid)
        _reset_project_session(st.session_state)
        st.session_state["current_project"] = {
            "project_id": selected_pid,
            "topic": proj_data["topic"],
            "language": proj_data.get("language", "en"),
            "status": proj_data.get("status", "searched"),
        }
        st.session_state["approved_topic"] = proj_data["topic"]
        st.session_state["language"] = proj_data.get("language", "en")
        if spec:
            st.session_state["draft_spec"]    = spec
            st.session_state["approved_spec"] = spec
        st.rerun()

    # ── Show current project info + edit ─────────────────────────────────────
    else:
        proj_meta = next((p for p in projects if p["project_id"] == current_pid), None)
        if proj_meta:
            status     = proj_meta.get("status", "searched")
            status_cls = _STATUS_CSS.get(status, "status-searched")
            st.markdown(
                f'<span class="proj-status {status_cls}">{status}</span> '
                f'<span style="font-size:11px;color:#9ca3af">{proj_meta.get("language","").upper()}'
                f' · {proj_meta.get("scene_count",0)} scenes</span>',
                unsafe_allow_html=True,
            )

        with st.expander("✏️ Edit project"):
            with st.form("edit_project_form"):
                edit_topic = st.text_input(
                    "Topic",
                    value=(proj_meta or {}).get("topic", ""),
                )
                edit_lang = st.selectbox(
                    "Language",
                    _LANG_OPTIONS,
                    index=_LANG_OPTIONS.index(
                        (proj_meta or {}).get("language", "en")
                        if (proj_meta or {}).get("language", "en") in _LANG_OPTIONS
                        else "en"
                    ),
                )
                col_save, col_del = st.columns(2)
                save_edit = col_save.form_submit_button("Save", type="primary")
                delete    = col_del.form_submit_button("Delete", type="secondary")

            if save_edit and edit_topic.strip():
                spec_dict = load_project_spec(current_pid)
                save_project(current_pid, edit_topic.strip(), edit_lang, status, spec_dict)
                st.session_state["current_project"]["topic"]    = edit_topic.strip()
                st.session_state["current_project"]["language"] = edit_lang
                st.session_state["approved_topic"] = edit_topic.strip()
                st.session_state["language"] = edit_lang
                st.rerun()

            if delete:
                import json
                from webui.storage import DATA_DIR
                from webui.state import delete_project as _db_delete
                proj_file = DATA_DIR / "projects.json"
                if proj_file.exists():
                    all_p = json.loads(proj_file.read_text(encoding="utf-8"))
                    all_p = [p for p in all_p if p["project_id"] != current_pid]
                    proj_file.write_text(json.dumps(all_p, ensure_ascii=False, indent=2), encoding="utf-8")
                _db_delete(current_pid)
                st.session_state.pop("current_project", None)
                _reset_project_session(st.session_state)
                st.rerun()

        # Save button
        current_spec = st.session_state.get("approved_spec") or st.session_state.get("draft_spec")
        if current_spec:
            from webui.state import save_spec
            from app.models.video_spec import VideoSpec
            if st.button("💾 Save", key="sidebar_save"):
                save_spec(VideoSpec.model_validate(current_spec))
                st.success("Saved")

    st.divider()

    # ── Stage navigation (only when a project is active) ─────────────────────
    PAGES = {
        "🔄  Workflow":       "workflow",
        "🔍  Topic Search":   "topic_review",
        "📝  Script":         "script_review",
        "🎬  Scene QA":       "scene_review",
        "🔊  Voiceover":      "final_render",
    }

    if selected_pid is not None:
        pending_stage = st.session_state.pop("pending_stage_nav", None)
        if pending_stage in PAGES:
            st.session_state["stage_nav"] = pending_stage

        page = st.radio(
            "Stage",
            list(PAGES.keys()),
            key="stage_nav",
            label_visibility="collapsed",
        )
    else:
        page = None
        st.caption("Select or create a project above.")

    st.divider()
    st.caption("Video Agent · v0.1")

# ── Route ─────────────────────────────────────────────────────────────────────

if page is None:
    st.title("🎬 Video Agent")
    st.info("Create or select a project in the sidebar to get started.")

elif "Workflow" in page:
    from webui.pages import workflow
    workflow.render()
elif "Topic" in page:
    from webui.pages import topic_review
    topic_review.render()
elif "Script" in page:
    from webui.pages import script_review
    script_review.render()
elif "Scene" in page:
    from webui.pages import scene_review
    scene_review.render()
elif "Voiceover" in page:
    from webui.pages import final_render
    final_render.render()
