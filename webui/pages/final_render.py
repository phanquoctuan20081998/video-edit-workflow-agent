"""Stage 4-6 UI: voiceover, composite, and final render.

Background-task pattern mirrors topic_review.py:
  - Pipeline runs in a daemon thread via ThreadPoolExecutor.
  - Results land in a @st.cache_resource dict (survives page navigation).
  - A st.fragment(run_every=3) polls without a full page rerun.
  - Navigating to another tab and back re-attaches to the running task.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import time
import uuid
from pathlib import Path

import streamlit as st


# ── Background task infrastructure ───────────────────────────────────────────

@st.cache_resource
def _render_store() -> dict:
    """Module-level singleton: survives page navigation. {run_key: {status, log, ...}}"""
    return {}


@st.cache_resource
def _render_executor() -> concurrent.futures.ThreadPoolExecutor:
    return concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="final_render")


@st.fragment(run_every=3)
def _render_poll(run_key: str) -> None:
    store = _render_store()
    task = store.get(run_key, {})
    if task.get("status") == "running":
        elapsed = int(time.monotonic() - task.get("started_at", time.monotonic()))
        stage = task.get("stage", "Starting…")
        st.info(f"🎬 {stage} ({elapsed}s elapsed)")
        for msg in task.get("log", []):
            st.caption(f"▸ {msg}")
    else:
        st.rerun()


def _submit_render(run_key: str, spec_dict: dict, pid: str) -> None:
    store = _render_store()
    store[run_key] = {"status": "running", "stage": "Starting…", "log": [], "started_at": time.monotonic(), "pid": pid}

    def _worker():
        try:
            from app.config import get_settings
            from app.models.video_spec import ProjectStatus, VideoSpec
            from app.pipeline.voiceover import run_voiceover
            from app.pipeline.composite import run_composite
            from app.pipeline.render import run_render
            from webui.storage import update_project_status

            cfg = get_settings()
            spec = VideoSpec.model_validate(spec_dict)

            store[run_key]["stage"] = "Synthesizing voiceover"
            store[run_key]["log"].append(f"TTS for {len(spec.scenes)} scenes…")
            spec = asyncio.run(run_voiceover(spec, artifact_dir=cfg.artifact_dir))
            spec.status = ProjectStatus.voiced
            update_project_status(pid, ProjectStatus.voiced.value)
            store[run_key]["log"].append("Voiceover done.")

            store[run_key]["stage"] = "Compositing scene clips"
            store[run_key]["log"].append("Assembling timeline…")
            composite_path = asyncio.run(run_composite(spec, artifact_dir=cfg.artifact_dir))
            spec.status = ProjectStatus.composited
            update_project_status(pid, ProjectStatus.composited.value)
            store[run_key]["log"].append("Composite done.")

            store[run_key]["stage"] = "Muxing final video"
            store[run_key]["log"].append("Encoding H.264…")
            final_path = asyncio.run(
                run_render(spec, composite_path=composite_path, artifact_dir=cfg.artifact_dir)
            )
            spec.final_video_path = final_path
            spec.status = ProjectStatus.rendered
            update_project_status(pid, ProjectStatus.rendered.value)
            store[run_key]["log"].append("Render done.")

            store[run_key] = {"status": "done", "spec_dict": spec.model_dump(), "final_path": final_path, "pid": pid}
        except Exception as e:
            store[run_key] = {"status": "error", "error": str(e), "pid": pid}

    _render_executor().submit(_worker)


def render():
    st.title("Stage 4-6 — Voiceover + Render")

    spec_dict = st.session_state.get("qa_approved_spec") or st.session_state.get("approved_spec")
    if not spec_dict:
        st.warning("No QA-approved script. Complete **Scene QA** first.")
        return

    from app.config import get_settings
    from app.models.video_spec import VideoSpec

    cfg = get_settings()
    spec = VideoSpec.model_validate(spec_dict)
    composite_path = str(Path(cfg.artifact_dir) / spec.project_id / "composite.mp4")

    st.markdown(f"**Topic:** {spec.topic}")
    st.markdown(f"**Project:** `{spec.project_id}`")

    store = _render_store()
    run_key = st.session_state.get("render_run_key")

    # ── Check running / completed task ────────────────────────────────────────
    if run_key and run_key in store:
        task = store[run_key]
        if task["status"] == "running":
            _render_status(spec, composite_path)
            _render_poll(run_key)
            return
        elif task["status"] == "done":
            task_pid = task.get("pid", spec.project_id)
            del store[run_key]
            st.session_state.pop("render_run_key", None)
            if task_pid == spec.project_id:
                result_spec_dict = task["spec_dict"]
                result_spec = VideoSpec.model_validate(result_spec_dict)
                st.session_state["approved_spec"] = result_spec_dict
                st.session_state["qa_approved_spec"] = result_spec_dict
                from webui.state import save_spec
                save_spec(result_spec)
            st.rerun()
            return
        elif task["status"] == "error":
            st.error(f"Render failed: {task['error']}")
            del store[run_key]
            st.session_state.pop("render_run_key", None)

    _render_status(spec, composite_path)

    if st.button("Run Voiceover + Final Render", type="primary"):
        key = str(uuid.uuid4())[:8]
        st.session_state["render_run_key"] = key
        _submit_render(key, spec_dict, spec.project_id)
        st.rerun()

    st.divider()
    if spec.final_video_path and Path(spec.final_video_path).exists():
        st.subheader("Final Video")
        st.video(spec.final_video_path)
        st.code(spec.final_video_path)
    elif Path(composite_path).exists():
        st.subheader("Composite Preview")
        st.video(composite_path)
        st.caption(f"Composite: `{composite_path}`")


def _render_status(spec, composite_path: str) -> None:
    voiced = sum(1 for scene in spec.scenes if scene.audio_path and scene.duration_sec)
    animated = sum(1 for scene in spec.scenes if scene.clip_path)

    cols = st.columns(4)
    cols[0].metric("Scenes", len(spec.scenes))
    cols[1].metric("Animated", animated)
    cols[2].metric("Voiced", voiced)
    cols[3].metric("Status", spec.status.value)

    missing_clips = [scene.id for scene in spec.scenes if not scene.clip_path]
    if missing_clips:
        st.warning(f"Missing scene clips: {', '.join(missing_clips)}")

    missing_audio = [scene.id for scene in spec.scenes if not scene.audio_path]
    if missing_audio:
        st.info(f"Voiceover not generated yet for: {', '.join(missing_audio)}")

    if Path(composite_path).exists():
        st.caption(f"Composite: `{composite_path}`")
