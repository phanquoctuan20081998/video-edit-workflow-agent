"""HITL Page 2 — Script (VideoSpec) review and approval."""

from __future__ import annotations

import asyncio

import streamlit as st


def render():
    st.title("Stage 2 — Script Review")

    topic = st.session_state.get("approved_topic", "")
    language = st.session_state.get("language", "vi")

    if not topic:
        st.warning("No approved topic found. Go to **Topic Review** first.")
        return

    st.markdown(f"**Topic:** {topic} | **Language:** {language}")

    # ── Run script agent ───────────────────────────────────────────────────────
    if st.button("Generate Script", type="primary"):
        with st.spinner("Researching and generating script..."):
            try:
                from app.agents.script import ScriptAgent
                agent = ScriptAgent()
                spec = asyncio.run(agent.run(topic=topic, language=language))
                if not spec.scenes:
                    raise ValueError("Script generation completed, but no scenes were returned.")
                st.session_state["draft_spec"] = spec.model_dump()
                from webui.state import save_spec
                save_spec(spec)
            except Exception as exc:
                st.session_state.pop("draft_spec", None)
                st.error(f"Script generation failed: {exc}")
                return

    spec_dict = st.session_state.get("draft_spec")
    if not spec_dict:
        st.info("Click 'Generate Script' to create a VideoSpec.")
        return

    from app.models.video_spec import VideoSpec
    spec = VideoSpec.model_validate(spec_dict)

    st.markdown(f"**{len(spec.scenes)} scenes** | project_id: `{spec.project_id}`")
    if not spec.scenes:
        st.error("This draft has 0 scenes. Click **Generate Script** again to create a new draft.")
        st.session_state.pop("approved_spec", None)
        return
    st.divider()

    # Editable scene table
    edited_scenes = []
    for i, scene in enumerate(spec.scenes):
        with st.expander(f"Scene {scene.order}: {scene.visual_type.value}", expanded=(i < 2)):
            cols = st.columns([1, 2])
            with cols[0]:
                visual_type = st.selectbox(
                    "Visual type",
                    ["manim", "chart", "title_card", "stock", "static_image"],
                    index=["manim", "chart", "title_card", "stock", "static_image"].index(scene.visual_type.value),
                    key=f"vt_{i}",
                )
                visual_spec = st.text_area("Visual spec", scene.visual_spec, height=100, key=f"vs_{i}")
            with cols[1]:
                narration = st.text_area("Narration", scene.narration, height=150, key=f"narr_{i}")

            edited_scenes.append({
                "id": scene.id,
                "order": scene.order,
                "narration": narration,
                "visual_type": visual_type,
                "visual_spec": visual_spec,
            })

    st.divider()
    if st.button("Approve Script → Start Animation", type="primary"):
        # Rebuild spec with edits
        from app.models.video_spec import ProjectStatus, Scene, VisualType
        new_scenes = [
            Scene(
                id=s["id"],
                order=s["order"],
                narration=s["narration"],
                visual_type=VisualType(s["visual_type"]),
                visual_spec=s["visual_spec"],
            )
            for s in edited_scenes
        ]
        spec.scenes = new_scenes
        spec.status = ProjectStatus.approved
        st.session_state["approved_spec"] = spec.model_dump()
        from webui.state import save_spec
        save_spec(spec)
        st.success(f"Script approved. {len(new_scenes)} scenes queued for animation.")
