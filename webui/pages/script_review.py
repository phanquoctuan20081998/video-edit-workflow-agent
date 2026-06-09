"""Stage 2 — Script review: generate VideoSpec, history, edit and select."""

from __future__ import annotations

import asyncio
from datetime import datetime

import streamlit as st

from webui.storage import load_scripts, load_script, save_script, save_project


_VISUAL_TYPES = ["manim", "chart", "title_card", "stock", "static_image"]


def _fmt_dt(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso[:16]


def _generate_script(topic: str, language: str) -> dict:
    from app.agents.script import ScriptAgent
    agent = ScriptAgent()
    spec = asyncio.run(agent.run(topic=topic, language=language))
    return spec.model_dump()


def _build_spec_from_edits(spec_dict: dict, edited_scenes: list[dict]) -> dict:
    """Merge edited scene fields back into spec dict."""
    from app.models.video_spec import Scene, VisualType
    from app.models.video_spec import VideoSpec

    spec = VideoSpec.model_validate(spec_dict)
    new_scenes = [
        Scene(
            id=s["id"],
            order=s["order"],
            narration=s["narration"],
            visual_type=VisualType(s["visual_type"]),
            visual_spec=s["visual_spec"],
            beats=s.get("beats", []),
        )
        for s in edited_scenes
    ]
    spec.scenes = new_scenes
    return spec.model_dump()


def render() -> None:
    st.title("Stage 2 — Script")

    topic    = st.session_state.get("approved_topic", "")
    language = st.session_state.get("language", "en")
    proj     = st.session_state.get("current_project") or {}

    if not topic:
        st.warning("No approved topic. Go to **Topic Search** first.")
        return

    st.markdown(f"**Topic:** {topic} &nbsp;|&nbsp; **Language:** `{language}`", unsafe_allow_html=True)

    # ── Generate new script ───────────────────────────────────────────────────
    if st.button("Generate New Script", type="primary"):
        with st.spinner(f"Researching and generating script in **{language}**..."):
            try:
                spec_dict = _generate_script(topic, language)
                pid = proj.get("project_id", "")
                script_id = save_script(pid, topic, language, spec_dict)
                save_project(pid, topic, language, "scripted", spec_dict)
                st.session_state["draft_spec"] = spec_dict
                st.session_state["active_script_id"] = script_id
                if "current_project" in st.session_state:
                    st.session_state["current_project"]["status"] = "scripted"
                st.rerun()
            except Exception as e:
                st.error(f"Script generation failed: {e}")
                return

    st.divider()

    # ── Script history ────────────────────────────────────────────────────────
    all_scripts = load_scripts()
    pid = proj.get("project_id", "")
    # Show scripts for current project first, then all others
    proj_scripts  = [s for s in all_scripts if s.get("project_id") == pid]
    other_scripts = [s for s in all_scripts if s.get("project_id") != pid]
    history = proj_scripts + other_scripts

    if history:
        with st.expander(f"📋 Script History ({len(history)} saved)", expanded=False):
            for entry in history:
                h_cols = st.columns([2, 4, 1, 1, 1])
                h_cols[0].caption(_fmt_dt(entry.get("timestamp", "")))
                h_cols[1].caption(entry.get("topic", "")[:50])
                h_cols[2].caption(entry.get("language", "").upper())
                h_cols[3].caption(f"{entry.get('scene_count', 0)} scenes")
                if h_cols[4].button("Load", key=f"hist_s_{entry['script_id']}"):
                    full = load_script(entry["script_id"])
                    if full:
                        st.session_state["draft_spec"] = full["spec"]
                        st.session_state["active_script_id"] = entry["script_id"]
                        st.rerun()

    # ── Current script editing ────────────────────────────────────────────────
    spec_dict = st.session_state.get("draft_spec")
    if not spec_dict:
        st.info("Click **Generate New Script** or load one from history.")
        return

    from app.models.video_spec import VideoSpec
    spec = VideoSpec.model_validate(spec_dict)

    active_id = st.session_state.get("active_script_id", "")
    st.markdown(
        f"**Editing:** `{active_id[:24]}…` &nbsp;|&nbsp; "
        f"**{len(spec.scenes)} scenes** &nbsp;|&nbsp; project `{spec.project_id[:8]}…`",
        unsafe_allow_html=True,
    )

    edited_scenes = []
    for i, scene in enumerate(spec.scenes):
        with st.expander(
            f"Scene {scene.order} — {scene.visual_type.value}  ·  "
            f"{len(scene.narration.split())} words  ·  {len(scene.beats)} beats",
            expanded=(i < 2),
        ):
            cols = st.columns([1, 2])
            with cols[0]:
                visual_type = st.selectbox(
                    "Visual type",
                    _VISUAL_TYPES,
                    index=_VISUAL_TYPES.index(scene.visual_type.value),
                    key=f"vt_{i}",
                )
                visual_spec = st.text_area("Visual spec", scene.visual_spec, height=100, key=f"vs_{i}")
            with cols[1]:
                narration = st.text_area("Narration", scene.narration, height=150, key=f"narr_{i}")

            # Beats preview (read-only)
            if scene.beats:
                with st.expander(f"Beats ({len(scene.beats)})", expanded=False):
                    for b in scene.beats:
                        st.markdown(
                            f"**{b.id}** · `{b.trigger_phrase}` → {b.visual_action}"
                        )

            edited_scenes.append({
                "id":          scene.id,
                "order":       scene.order,
                "narration":   narration,
                "visual_type": visual_type,
                "visual_spec": visual_spec,
                "beats":       [bt.model_dump() for bt in scene.beats],
            })

    st.divider()
    col_save, col_approve = st.columns(2)

    if col_save.button("💾 Save Edits"):
        updated = _build_spec_from_edits(spec_dict, edited_scenes)
        st.session_state["draft_spec"] = updated
        pid = proj.get("project_id", spec.project_id)
        sid = save_script(pid, topic, language, updated)
        st.session_state["active_script_id"] = sid
        st.success("Edits saved as new history entry.")

    if col_approve.button("Approve Script → Start Animation", type="primary"):
        updated = _build_spec_from_edits(spec_dict, edited_scenes)
        pid = proj.get("project_id", spec.project_id)
        save_project(pid, topic, language, "approved", updated)
        if "current_project" in st.session_state:
            st.session_state["current_project"]["status"] = "approved"
        st.session_state["approved_spec"] = updated
        st.success(f"Script approved. {len(edited_scenes)} scenes queued for animation.")
