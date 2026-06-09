"""Stage 3 — Scene QA: render Manim scenes, history per scene, select best variant.

Background-task pattern mirrors topic_review.py:
  - Codegen runs in a daemon thread via ThreadPoolExecutor.
  - Results land in a @st.cache_resource dict (survives page navigation).
  - A st.fragment(run_every=3) polls without a full page rerun.
  - Navigating to another tab and back re-attaches to the running task.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import time
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st

from webui.storage import load_scene_renders, save_scene_render, save_project


def _fmt_dt(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso[:16]


# ── Background task infrastructure ───────────────────────────────────────────

@st.cache_resource
def _scene_store() -> dict:
    """Module-level singleton: survives page navigation. {run_key: {status, log, ...}}"""
    return {}


@st.cache_resource
def _scene_executor() -> concurrent.futures.ThreadPoolExecutor:
    return concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="manim_codegen")


def _log_terminal(lines: list[str], max_lines: int = 60) -> None:
    """Render last N log lines as a dark terminal block."""
    if not lines:
        return
    visible = lines[-max_lines:]
    st.code("\n".join(visible), language=None)


@st.fragment(run_every=3)
def _run_all_poll(run_key: str) -> None:
    store = _scene_store()
    task = store.get(run_key, {})
    if task.get("status") == "running":
        elapsed = int(time.monotonic() - task.get("started_at", time.monotonic()))
        st.info(f"🎬 Manim codegen running… ({elapsed}s elapsed)")
        log_lines = task.get("log", [])
        if log_lines:
            with st.expander(f"Agent log ({len(log_lines)} lines)", expanded=True):
                _log_terminal(log_lines)
    else:
        st.rerun()


@st.fragment(run_every=3)
def _regen_banner(running_scene_ids: tuple) -> None:
    store = _scene_store()
    any_running = False
    for sid in running_scene_ids:
        run_key = st.session_state.get(f"regen_run_{sid}")
        if not run_key:
            continue
        task = store.get(run_key, {})
        if task.get("status") == "running":
            any_running = True
            elapsed = int(time.monotonic() - task.get("started_at", time.monotonic()))
            st.info(f"⏳ Re-generating **{sid}**… ({elapsed}s elapsed)")
            log_lines = task.get("log", [])
            if log_lines:
                with st.expander(f"Agent log — {sid} ({len(log_lines)} lines)", expanded=True):
                    _log_terminal(log_lines)
    if not any_running:
        st.rerun()


def _submit_render_all(run_key: str, spec_dict: dict, pid: str, max_repairs: int) -> None:
    store = _scene_store()
    store[run_key] = {"status": "running", "log": [], "started_at": time.monotonic()}

    def _append_log(msg: str) -> None:
        store[run_key]["log"].append(msg)

    def _progress_cb(scene_id: str, status: str) -> None:
        _append_log(f"[{scene_id}] ▶ {status}")

    def _worker():
        try:
            from app.agents.manim_codegen import run_manim_codegen
            from app.models.video_spec import VideoSpec
            _append_log("Starting Manim codegen for all scenes…")
            spec = VideoSpec.model_validate(spec_dict)
            result_spec = asyncio.run(
                run_manim_codegen(
                    spec,
                    max_repairs=max_repairs,
                    progress_cb=_progress_cb,
                    log_cb=_append_log,
                )
            )
            _append_log(f"✅ Completed {len(result_spec.scenes)} scenes")
            store[run_key] = {"status": "done", "spec_dict": result_spec.model_dump(), "pid": pid}
        except Exception as e:
            _append_log(f"❌ Error: {e}")
            store[run_key] = {"status": "error", "error": str(e)}

    _scene_executor().submit(_worker)


def _submit_render_one(run_key: str, scene_id: str, spec_dict: dict, pid: str, max_repairs: int) -> None:
    store = _scene_store()
    store[run_key] = {"status": "running", "log": [], "started_at": time.monotonic()}

    def _append_log(msg: str) -> None:
        store[run_key]["log"].append(msg)

    def _progress_cb(sid: str, status: str) -> None:
        _append_log(f"[{sid}] ▶ {status}")

    def _worker():
        try:
            from app.agents.manim_codegen import render_scene
            from app.models.video_spec import VideoSpec
            _append_log(f"Re-generating {scene_id}…")
            spec = VideoSpec.model_validate(spec_dict)
            scene = next(s for s in spec.scenes if s.id == scene_id)
            scene.manim_code = None
            scene.manim_code_hash = None
            asyncio.run(
                render_scene(
                    scene,
                    spec,
                    max_repairs=max_repairs,
                    progress_cb=_progress_cb,
                    log_cb=_append_log,
                )
            )
            _append_log(f"✅ {scene_id} done")
            store[run_key] = {"status": "done", "spec_dict": spec.model_dump(), "scene_id": scene_id, "pid": pid}
        except Exception as e:
            _append_log(f"❌ Error: {e}")
            store[run_key] = {"status": "error", "error": str(e)}

    _scene_executor().submit(_worker)


def render() -> None:
    st.title("Stage 3 — Scene QA")

    spec_dict = st.session_state.get("approved_spec")
    if not spec_dict:
        st.warning("No approved script. Complete **Script** review first.")
        return

    from app.models.video_spec import VideoSpec
    spec = VideoSpec.model_validate(spec_dict)
    proj = st.session_state.get("current_project") or {}
    pid  = proj.get("project_id", spec.project_id)
    store = _scene_store()

    # ── Process completed "run all" task ──────────────────────────────────────
    run_key = st.session_state.get("scene_run_key")
    if run_key and run_key in store:
        task = store[run_key]
        if task["status"] == "running":
            st.markdown(
                f"**Topic:** {spec.topic} &nbsp;|&nbsp; **Scenes:** {len(spec.scenes)} &nbsp;|&nbsp; "
                f"**Language:** `{spec.language}`",
                unsafe_allow_html=True,
            )
            _run_all_poll(run_key)
            return
        elif task["status"] == "done":
            task_pid = task.get("pid", pid)
            if task_pid != pid:
                del store[run_key]
                st.session_state.pop("scene_run_key", None)
                st.rerun()
                return
            result_spec_dict = task["spec_dict"]
            result_spec = VideoSpec.model_validate(result_spec_dict)
            st.session_state["approved_spec"] = result_spec_dict
            for scene in result_spec.scenes:
                if scene.clip_path:
                    save_scene_render(task_pid, scene.id, scene.model_dump())
            save_project(task_pid, result_spec.topic, result_spec.language, "animated", result_spec_dict)
            if "current_project" in st.session_state:
                st.session_state["current_project"]["status"] = "animated"
            from webui.state import save_spec
            save_spec(result_spec)
            del store[run_key]
            st.session_state.pop("scene_run_key", None)
            st.rerun()
            return
        elif task["status"] == "error":
            st.error(f"Codegen failed: {task['error']}")
            del store[run_key]
            st.session_state.pop("scene_run_key", None)

    # ── Process completed single-scene regen tasks ────────────────────────────
    for scene in spec.scenes:
        regen_key = st.session_state.get(f"regen_run_{scene.id}")
        if not regen_key or regen_key not in store:
            continue
        task = store[regen_key]
        if task["status"] == "done":
            task_pid = task.get("pid", pid)
            scene_id = task.get("scene_id", scene.id)
            del store[regen_key]
            st.session_state.pop(f"regen_run_{scene.id}", None)
            if task_pid == pid:
                result_spec_dict = task["spec_dict"]
                result_spec = VideoSpec.model_validate(result_spec_dict)
                st.session_state["approved_spec"] = result_spec_dict
                done_scene = next((s for s in result_spec.scenes if s.id == scene_id), None)
                if done_scene:
                    save_scene_render(task_pid, scene_id, done_scene.model_dump())
                from webui.state import save_spec
                save_spec(result_spec)
            st.rerun()
            return
        elif task["status"] == "error":
            st.error(f"Re-generate {scene.id} failed: {task['error']}")
            del store[regen_key]
            st.session_state.pop(f"regen_run_{scene.id}", None)

    st.markdown(
        f"**Topic:** {spec.topic} &nbsp;|&nbsp; **Scenes:** {len(spec.scenes)} &nbsp;|&nbsp; "
        f"**Language:** `{spec.language}`",
        unsafe_allow_html=True,
    )

    # ── Banner for running single-scene regen tasks ───────────────────────────
    running_regen_ids = tuple(
        s.id for s in spec.scenes
        if st.session_state.get(f"regen_run_{s.id}")
        and store.get(st.session_state[f"regen_run_{s.id}"], {}).get("status") == "running"
    )
    if running_regen_ids:
        _regen_banner(running_regen_ids)

    # ── Global controls ───────────────────────────────────────────────────────
    col1, col2 = st.columns([1, 2])
    max_repairs = col1.slider("Max repair attempts per scene", 1, 6, 4)

    if col2.button("▶ Run Manim Codegen (All Scenes)", type="primary"):
        key = str(uuid.uuid4())[:8]
        st.session_state["scene_run_key"] = key
        save_project(pid, spec.topic, spec.language, "animating")
        if "current_project" in st.session_state:
            st.session_state["current_project"]["status"] = "animating"
        _submit_render_all(key, spec_dict, pid, max_repairs)
        st.rerun()

    st.divider()

    # ── Per-scene panels ──────────────────────────────────────────────────────
    all_approved = True
    for scene in sorted(spec.scenes, key=lambda s: s.order):
        scene_renders = load_scene_renders(pid, scene.id)
        regen_key = st.session_state.get(f"regen_run_{scene.id}")
        regen_running = bool(regen_key and store.get(regen_key, {}).get("status") == "running")

        with st.expander(
            f"Scene {scene.order} — {scene.id}  ·  {scene.visual_type.value}  ·  "
            f"{len(scene.beats)} beats",
            expanded=True,
        ):
            left, right = st.columns([2, 3])

            with left:
                if regen_running:
                    st.info("⏳ Re-generating in background — see banner above.")
                    all_approved = False
                else:
                    clip_path = scene.clip_path
                    qa_passed = scene.clip_qa_passed

                    if scene_renders:
                        render_labels = [
                            f"{_fmt_dt(r['timestamp'])} — "
                            f"{'✅ QA pass' if r['scene'].get('clip_qa_passed') else '⚠️ QA fail'}"
                            for r in scene_renders
                        ]
                        chosen_idx = st.selectbox(
                            "Render history",
                            range(len(render_labels)),
                            format_func=lambda i: render_labels[i],
                            key=f"hist_sel_{scene.id}",
                        )
                        chosen_scene_dict = scene_renders[chosen_idx]["scene"]
                        clip_path = chosen_scene_dict.get("clip_path")
                        qa_passed = chosen_scene_dict.get("clip_qa_passed")

                        if st.button("Use this render", key=f"use_{scene.id}_{chosen_idx}"):
                            for s in spec.scenes:
                                if s.id == scene.id:
                                    s.clip_path       = clip_path
                                    s.clip_qa_passed  = qa_passed
                                    s.manim_code      = chosen_scene_dict.get("manim_code")
                                    s.manim_code_hash = chosen_scene_dict.get("manim_code_hash")
                            st.session_state["approved_spec"] = spec.model_dump()
                            st.rerun()

                    if clip_path and Path(clip_path).exists():
                        st.video(clip_path)
                        st.markdown("✅ QA passed" if qa_passed else "⚠️ QA flagged")
                    else:
                        st.info("Not rendered yet." if not scene.manim_code else "Render failed. Check the generated Manim code below.")
                        all_approved = False

                override_pass = st.checkbox("Manually approve this scene", key=f"approve_{scene.id}")
                if override_pass and scene.clip_path and Path(scene.clip_path).exists():
                    scene.clip_qa_passed = True

            with right:
                st.markdown(f"**Narration:** {scene.narration}")
                st.markdown(f"**Visual spec:** {scene.visual_spec}")

                if scene.beats:
                    with st.expander(f"Beats ({len(scene.beats)})", expanded=False):
                        for b in scene.beats:
                            st.markdown(f"**{b.id}** · `{b.trigger_phrase}` → {b.visual_action}")

                if scene.manim_code:
                    with st.expander("Manim code"):
                        st.code(scene.manim_code, language="python")

                if not scene.clip_qa_passed and not override_pass:
                    all_approved = False
                    if regen_running:
                        st.caption("Re-generating in background — see banner above.")
                    elif st.button(f"Re-generate {scene.id}", key=f"regen_{scene.id}"):
                        key = str(uuid.uuid4())[:8]
                        st.session_state[f"regen_run_{scene.id}"] = key
                        _submit_render_one(key, scene.id, spec_dict, pid, max_repairs)
                        st.rerun()

    st.divider()

    if all_approved:
        if st.button("Proceed to Voiceover + Render", type="primary"):
            save_project(pid, spec.topic, spec.language, "animated", spec.model_dump())
            if "current_project" in st.session_state:
                st.session_state["current_project"]["status"] = "animated"
            st.session_state["qa_approved_spec"] = spec.model_dump()
            from webui.state import save_spec
            save_spec(spec)
            st.session_state["pending_stage_nav"] = "🔊  Voiceover"
            st.rerun()
    else:
        st.warning("All scenes must be approved (or manually overridden) before proceeding.")
