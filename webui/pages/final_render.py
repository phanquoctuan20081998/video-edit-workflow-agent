"""Stage 4-6 UI: voiceover, composite, and final render."""

from __future__ import annotations

import asyncio
from pathlib import Path

import streamlit as st


def render():
    st.title("Stage 4-6 — Voiceover + Render")

    spec_dict = st.session_state.get("qa_approved_spec") or st.session_state.get("approved_spec")
    if not spec_dict:
        st.warning("No QA-approved script. Complete **Scene QA** first.")
        return

    from app.config import get_settings
    from app.models.video_spec import ProjectStatus, VideoSpec

    cfg = get_settings()
    spec = VideoSpec.model_validate(spec_dict)
    composite_path = str(Path(cfg.artifact_dir) / spec.project_id / "composite.mp4")

    st.markdown(f"**Topic:** {spec.topic}")
    st.markdown(f"**Project:** `{spec.project_id}`")

    _render_status(spec, composite_path)

    if st.button("Run Voiceover + Final Render", type="primary"):
        with st.spinner("Synthesizing voiceover..."):
            from app.pipeline.voiceover import run_voiceover
            spec = asyncio.run(run_voiceover(spec, artifact_dir=cfg.artifact_dir))
            spec.status = ProjectStatus.voiced
            _persist(spec)

        with st.spinner("Compositing scene clips..."):
            from app.pipeline.composite import run_composite
            composite_path = asyncio.run(run_composite(spec, artifact_dir=cfg.artifact_dir))
            spec.status = ProjectStatus.composited
            _persist(spec)

        with st.spinner("Muxing final video..."):
            from app.pipeline.render import run_render
            final_path = asyncio.run(
                run_render(spec, composite_path=composite_path, artifact_dir=cfg.artifact_dir)
            )
            spec.final_video_path = final_path
            spec.status = ProjectStatus.rendered
            _persist(spec)

        st.success("Final video rendered.")
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


def _persist(spec):
    from webui.state import save_spec

    spec_dict = spec.model_dump()
    st.session_state["approved_spec"] = spec_dict
    st.session_state["qa_approved_spec"] = spec_dict
    save_spec(spec)


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
