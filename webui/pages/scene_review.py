"""Stage 3 — Scene QA: render Manim scenes, history per scene, select best variant."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import streamlit as st

from webui.storage import load_scene_renders, save_scene_render, save_project


def _fmt_dt(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso[:16]


def _render_all_scenes(spec, max_repairs: int, progress_cb=None):
    from app.agents.manim_codegen import run_manim_codegen
    return asyncio.run(run_manim_codegen(spec, max_repairs=max_repairs, progress_cb=progress_cb))


def _render_one_scene(scene, spec, max_repairs: int, progress_cb=None):
    from app.agents.manim_codegen import render_scene
    asyncio.run(render_scene(scene, spec, max_repairs=max_repairs, progress_cb=progress_cb))


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

    st.markdown(
        f"**Topic:** {spec.topic} &nbsp;|&nbsp; **Scenes:** {len(spec.scenes)} &nbsp;|&nbsp; "
        f"**Language:** `{spec.language}`",
        unsafe_allow_html=True,
    )

    # ── Global controls ───────────────────────────────────────────────────────
    col1, col2 = st.columns([1, 2])
    max_repairs = col1.slider("Max repair attempts per scene", 1, 6, 4)
    run_all = col2.button("▶ Run Manim Codegen (All Scenes)", type="primary")

    if run_all:
        scene_ids = [s.id for s in spec.scenes]
        status_map: dict[str, str] = {sid: "⏳ queued" for sid in scene_ids}

        with st.status("Running Manim Codegen…", expanded=True) as status_box:
            rows = {sid: st.empty() for sid in scene_ids}
            for sid, placeholder in rows.items():
                placeholder.markdown(f"**{sid}** — {status_map[sid]}")

            def on_progress(scene_id: str, msg: str) -> None:
                status_map[scene_id] = msg
                rows[scene_id].markdown(f"**{scene_id}** — {msg}")

            try:
                spec = _render_all_scenes(spec, max_repairs, progress_cb=on_progress)
                spec_dict = spec.model_dump()
                st.session_state["approved_spec"] = spec_dict
                for scene in spec.scenes:
                    if scene.clip_path:
                        save_scene_render(pid, scene.id, scene.model_dump())
                save_project(pid, spec.topic, spec.language, "animated", spec_dict)
                if "current_project" in st.session_state:
                    st.session_state["current_project"]["status"] = "animated"
                from webui.state import save_spec
                save_spec(spec)
                status_box.update(label="Codegen complete ✅", state="complete", expanded=False)
            except Exception as e:
                status_box.update(label=f"Codegen failed: {e}", state="error")
                st.error(f"Codegen failed: {e}")
                return
        st.rerun()

    st.divider()

    # ── Per-scene panels ──────────────────────────────────────────────────────
    all_approved = True
    for scene in sorted(spec.scenes, key=lambda s: s.order):
        scene_renders = load_scene_renders(pid, scene.id)

        with st.expander(
            f"Scene {scene.order} — {scene.id}  ·  {scene.visual_type.value}  ·  "
            f"{len(scene.beats)} beats",
            expanded=True,
        ):
            left, right = st.columns([2, 3])

            with left:
                # Render history selector
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
                else:
                    clip_path = scene.clip_path
                    qa_passed = scene.clip_qa_passed

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
                    if st.button(f"Re-generate {scene.id}", key=f"regen_{scene.id}"):
                        with st.status(f"Re-generating {scene.id}…", expanded=True) as regen_status:
                            msg_slot = st.empty()
                            def _regen_cb(sid: str, msg: str) -> None:
                                msg_slot.markdown(f"**{sid}** — {msg}")
                            try:
                                scene.manim_code      = None
                                scene.manim_code_hash = None
                                _render_one_scene(scene, spec, max_repairs, progress_cb=_regen_cb)
                                spec_dict = spec.model_dump()
                                st.session_state["approved_spec"] = spec_dict
                                save_scene_render(pid, scene.id, scene.model_dump())
                                from webui.state import save_spec
                                save_spec(spec)
                                regen_status.update(label=f"{scene.id} done ✅", state="complete", expanded=False)
                            except Exception as e:
                                regen_status.update(label=f"Failed: {e}", state="error")
                                st.error(f"Re-generate failed: {e}")
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
            st.session_state["pending_stage_nav"] = "Voiceover + Render"
            st.rerun()
    else:
        st.warning("All scenes must be approved (or manually overridden) before proceeding.")
