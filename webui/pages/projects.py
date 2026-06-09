"""Projects page — create, list, and load projects."""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from webui.storage import load_projects, save_project, load_project_spec


_STATUS_COLOR = {
    "searched":   "#3b82f6",
    "scripted":   "#8b5cf6",
    "approved":   "#06b6d4",
    "animated":   "#f59e0b",
    "voiced":     "#10b981",
    "composited": "#6366f1",
    "rendered":   "#22c55e",
}


def _status_badge(status: str) -> str:
    color = _STATUS_COLOR.get(status, "#6b7280")
    return (
        f'<span style="background:{color};color:#fff;font-size:11px;font-weight:700;'
        f'padding:2px 8px;border-radius:10px;text-transform:uppercase;">{status}</span>'
    )


def _fmt_dt(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso[:16]


def render() -> None:
    st.title("Projects")

    # ── Create new project ────────────────────────────────────────────────────
    with st.expander("➕ Create New Project", expanded=False):
        with st.form("new_project_form"):
            topic   = st.text_input("Topic", placeholder="e.g. Fast Fourier Transform")
            language = st.selectbox("Language", ["en", "vi", "ja", "zh", "ko", "fr", "de", "es"], index=0)
            submitted = st.form_submit_button("Create Project", type="primary")

        if submitted and topic.strip():
            import uuid
            pid = str(uuid.uuid4())
            save_project(pid, topic.strip(), language, "searched")
            st.session_state["current_project"] = {
                "project_id": pid,
                "topic": topic.strip(),
                "language": language,
                "status": "searched",
            }
            st.success(f"Project created: **{topic.strip()}**")
            st.rerun()

    st.divider()

    # ── Project list ──────────────────────────────────────────────────────────
    projects = load_projects()
    if not projects:
        st.info("No projects yet. Create one above.")
        return

    st.markdown(f"**{len(projects)} project(s)**")

    current_pid = (st.session_state.get("current_project") or {}).get("project_id")

    for p in projects:
        pid     = p["project_id"]
        is_curr = pid == current_pid
        cols    = st.columns([3, 1, 1, 1, 1])

        # Title + active indicator
        title_md = f"**{'▶ ' if is_curr else ''}{p['topic']}**"
        cols[0].markdown(title_md)
        cols[0].markdown(
            _status_badge(p.get("status", "—")),
            unsafe_allow_html=True,
        )

        cols[1].caption(p.get("language", "").upper())
        cols[2].caption(f"{p.get('scene_count', 0)} scenes")
        cols[3].caption(_fmt_dt(p.get("updated_at", "")))

        if cols[4].button(
            "Load" if not is_curr else "Loaded ✓",
            key=f"load_{pid}",
            disabled=is_curr,
        ):
            spec = load_project_spec(pid)
            st.session_state["current_project"] = {
                "project_id": pid,
                "topic": p["topic"],
                "language": p.get("language", "en"),
                "status": p.get("status", "searched"),
            }
            if spec:
                st.session_state["approved_spec"] = spec
                st.session_state["approved_topic"] = p["topic"]
                st.session_state["language"] = p.get("language", "en")
            st.rerun()

    st.divider()
    if current_pid:
        curr = st.session_state["current_project"]
        st.success(
            f"Active: **{curr['topic']}** — {curr.get('status', '—')} — "
            f"{curr.get('language', '').upper()}"
        )
