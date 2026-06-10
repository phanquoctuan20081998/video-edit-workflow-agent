"""Stage 2 — Script review: generate VideoSpec, history, edit and select.

Background-task pattern mirrors topic_review.py:
  - Script generation runs in a daemon thread.
  - Results stored in @st.cache_resource dict (survives page navigation).
  - A st.fragment(run_every=2) polls without full page rerun (no flicker).
"""

from __future__ import annotations

import concurrent.futures
import time
import uuid
from datetime import datetime

import streamlit as st

from webui.storage import load_scripts, load_script, save_script, save_project


_VISUAL_TYPES = ["manim", "chart", "title_card", "stock", "static_image"]


# ── Background task store ─────────────────────────────────────────────────────

@st.cache_resource
def _script_store() -> dict:
    return {}


@st.cache_resource
def _script_executor() -> concurrent.futures.ThreadPoolExecutor:
    return concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="script_gen")


@st.fragment(run_every=2)
def _script_running_poll(run_key: str) -> None:
    store = _script_store()
    task = store.get(run_key, {})
    if task.get("status") == "running":
        elapsed = int(time.monotonic() - task.get("started_at", time.monotonic()))
        st.info(f"📝 Script generation running… ({elapsed}s elapsed)")
        for msg in task.get("log", []):
            st.caption(f"▸ {msg}")
    else:
        st.rerun()


def _start_script_task(
    run_key: str, topic: str, language: str, target_duration_sec: float | None = None
) -> None:
    store = _script_store()
    store[run_key] = {"status": "running", "log": [], "started_at": time.monotonic()}

    def _worker():
        try:
            import asyncio
            from app.agents.script import ScriptAgent
            store[run_key]["log"].append("Initializing script agent…")
            agent = ScriptAgent()
            store[run_key]["log"].append(f"Researching topic: {topic!r}…")
            if target_duration_sec:
                store[run_key]["log"].append(
                    f"Target length: {target_duration_sec / 60:.1f} min"
                )
            spec = asyncio.run(agent.run(
                topic=topic,
                language=language,
                target_duration_sec=target_duration_sec,
            ))
            store[run_key]["log"].append(f"Writing scenes ({language})…")
            store[run_key]["log"].append(f"Generated {len(spec.scenes)} scenes with beats")
            store[run_key] = {"status": "done", "spec": spec.model_dump()}
        except Exception as e:
            store[run_key] = {"status": "error", "error": str(e)}

    _script_executor().submit(_worker)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_dt(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso[:16]


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

    # ── Poll running script task ──────────────────────────────────────────────
    store   = _script_store()
    run_key = st.session_state.get("script_run_key")

    if run_key and run_key in store:
        task = store[run_key]
        if task["status"] == "running":
            _script_running_poll(run_key)
            return
        elif task["status"] == "done":
            spec_dict = task["spec"]
            if not spec_dict.get("scenes"):
                st.error("Script returned 0 scenes. Try generating again.")
            else:
                pid       = proj.get("project_id", "")
                script_id = save_script(pid, topic, language, spec_dict)
                save_project(pid, topic, language, "scripted", spec_dict)
                st.session_state["draft_spec"]       = spec_dict
                st.session_state["active_script_id"] = script_id
                if "current_project" in st.session_state:
                    st.session_state["current_project"]["status"] = "scripted"
            del store[run_key]
            st.session_state.pop("script_run_key", None)
            st.rerun()
            return
        elif task["status"] == "error":
            st.error(f"Script generation failed: {task['error']}")
            del store[run_key]
            st.session_state.pop("script_run_key", None)

    # ── Generate controls ─────────────────────────────────────────────────────
    gen_cols = st.columns([2, 3])
    with gen_cols[1]:
        target_min = st.slider(
            "🎯 Target video length (minutes)",
            min_value=1.0, max_value=15.0,
            value=float(st.session_state.get("target_minutes", 4.0)),
            step=0.5,
            help=(
                "The script agent budgets narration word counts against this length "
                "and rewrites the script once if the estimate drifts more than 20%."
            ),
            key="target_minutes",
        )
    with gen_cols[0]:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        if st.button("Generate Script", type="primary"):
            key = str(uuid.uuid4())[:8]
            st.session_state["script_run_key"] = key
            pid = proj.get("project_id", "")
            if pid:
                save_project(pid, topic, language, "scripting")
                st.session_state["current_project"]["status"] = "scripting"
            _start_script_task(key, topic, language, target_duration_sec=target_min * 60)
            st.rerun()

    st.divider()

    # ── Script history ────────────────────────────────────────────────────────
    all_scripts = load_scripts()
    pid = proj.get("project_id", "")
    history = [s for s in all_scripts if s.get("project_id") == pid]

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
        st.info("Click **Generate Script** or load one from history.")
        return

    from app.models.video_spec import VideoSpec
    spec = VideoSpec.model_validate(spec_dict)

    active_id = st.session_state.get("active_script_id", "")
    est_sec = spec.estimated_duration_sec()
    est_label = f"{int(est_sec // 60)}:{int(est_sec % 60):02d}"
    target = spec.target_duration_sec
    st.markdown(
        f"**Editing:** `{active_id[:24]}…` &nbsp;|&nbsp; "
        f"**{len(spec.scenes)} scenes** &nbsp;|&nbsp; "
        f"⏱️ est. **{est_label}** &nbsp;|&nbsp; project `{spec.project_id[:8]}…`",
        unsafe_allow_html=True,
    )
    if target:
        drift = (est_sec - target) / target
        if abs(drift) > 0.20:
            direction = "longer" if drift > 0 else "shorter"
            st.warning(
                f"Estimated length is {abs(drift):.0%} {direction} than the "
                f"{target / 60:.1f} min target. Edit narration below or regenerate."
            )
        else:
            st.caption(f"✅ Within ±20% of the {target / 60:.1f} min target.")
    if not spec.scenes:
        st.error("This draft has 0 scenes. Click **Generate Script** again to create a new draft.")
        st.session_state.pop("approved_spec", None)
        return
    st.divider()

    from app.models.video_spec import words_per_second
    wps = words_per_second(spec.language)
    edited_scenes = []
    for i, scene in enumerate(spec.scenes):
        scene_sec = scene.duration_sec or (len(scene.narration.split()) / wps)
        with st.expander(
            f"Scene {scene.order} — {scene.visual_type.value}  ·  "
            f"{len(scene.narration.split())} words (~{scene_sec:.0f}s)  ·  {len(scene.beats)} beats",
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
        updated["status"] = "approved"
        pid = proj.get("project_id", spec.project_id)
        save_project(pid, topic, language, "approved", updated)
        if "current_project" in st.session_state:
            st.session_state["current_project"]["status"] = "approved"
        st.session_state["approved_spec"] = updated
        from webui.state import save_spec
        from app.models.video_spec import VideoSpec as _VS
        save_spec(_VS.model_validate(updated))
        st.session_state["pending_stage_nav"] = "🎬  Scene QA"
        st.rerun()
