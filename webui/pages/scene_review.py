"""HITL Page 3 — Scene QA: view rendered clips, approve/reject per scene."""

from __future__ import annotations

import asyncio
from pathlib import Path

import streamlit as st


def render():
    st.title("Stage 3 — Scene QA")

    spec_dict = st.session_state.get("approved_spec")
    if not spec_dict:
        st.warning("No approved script. Complete **Script Review** first.")
        return

    from app.models.video_spec import VideoSpec
    spec = VideoSpec.model_validate(spec_dict)

    st.markdown(f"**Topic:** {spec.topic} | **Scenes:** {len(spec.scenes)}")

    # ── Run codegen ────────────────────────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        max_repairs = st.slider("Max repair attempts per scene", 1, 6, 4)
    with col2:
        run_all = st.button("Run Manim Codegen (All Scenes)", type="primary")

    if run_all:
        with st.spinner("Generating and rendering scenes..."):
            from app.agents.manim_codegen import run_manim_codegen
            spec = asyncio.run(run_manim_codegen(spec, max_repairs=max_repairs))
            st.session_state["approved_spec"] = spec.model_dump()
            from webui.state import save_spec
            save_spec(spec)
        st.rerun()

    st.divider()

    # Scene-by-scene QA table
    all_approved = True
    for scene in sorted(spec.scenes, key=lambda s: s.order):
        with st.expander(f"Scene {scene.order} — {scene.id}", expanded=True):
            cols = st.columns([2, 3])
            with cols[0]:
                if scene.clip_path and Path(scene.clip_path).exists():
                    st.video(scene.clip_path)
                    qa_status = "✅ QA passed" if scene.clip_qa_passed else "⚠️ QA failed (flagged)"
                    st.markdown(qa_status)
                else:
                    st.info("Not rendered yet." if not scene.manim_code else "Render failed. Check the generated Manim code below.")
                    all_approved = False

                override_pass = st.checkbox("Manually approve this scene", key=f"approve_{scene.id}")
                if override_pass and scene.clip_path and Path(scene.clip_path).exists():
                    scene.clip_qa_passed = True

            with cols[1]:
                st.markdown(f"**Narration:** {scene.narration}")
                st.markdown(f"**Visual spec:** {scene.visual_spec}")
                if scene.manim_code:
                    with st.expander("View Manim code"):
                        st.code(scene.manim_code, language="python")

                if not scene.clip_qa_passed and not override_pass:
                    regen = st.button(f"Re-generate scene {scene.id}", key=f"regen_{scene.id}")
                    if regen:
                        with st.spinner(f"Re-generating {scene.id}..."):
                            from app.agents.manim_codegen import render_scene
                            # Reset code to force re-generation
                            scene.manim_code = None
                            scene.manim_code_hash = None
                            asyncio.run(render_scene(scene, spec, max_repairs=max_repairs))
                            st.session_state["approved_spec"] = spec.model_dump()
                            from webui.state import save_spec
                            save_spec(spec)
                        st.rerun()
                    all_approved = False

    st.divider()
    if all_approved:
        if st.button("Proceed to Voiceover + Render", type="primary"):
            st.session_state["qa_approved_spec"] = spec.model_dump()
            from webui.state import save_spec
            save_spec(spec)
            st.session_state["pending_stage_nav"] = "Voiceover + Render"
            st.rerun()
    else:
        st.warning("All scenes must be approved (or manually overridden) before proceeding.")
