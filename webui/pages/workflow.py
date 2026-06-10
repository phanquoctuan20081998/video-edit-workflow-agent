"""Workflow page — animated pipeline visualization."""

from __future__ import annotations

import streamlit as st
from webui.storage import load_projects


_STAGES = [
    {"num": 1, "name": "Market Search", "desc": "Find trending topics", "hitl": True,  "key": "topic"},
    {"num": 2, "name": "Script",        "desc": "Research & write script", "hitl": True,  "key": "script"},
    {"num": 3, "name": "Manim Codegen", "desc": "Generate animations",   "hitl": False, "key": "manim"},
    {"num": 4, "name": "Voiceover",     "desc": "TTS + timing",           "hitl": False, "key": "voice"},
    {"num": 5, "name": "Composite",     "desc": "Assemble clips",         "hitl": False, "key": "composite"},
    {"num": 6, "name": "Render",        "desc": "Final encode",           "hitl": False, "key": "render"},
]

_STATUS_TO_STAGE = {
    "searching":   1,
    "searched":    1,
    "scripting":   2,
    "scripted":    2,
    "approved":    2,
    "animating":   3,
    "animated":    3,
    "voicing":     4,
    "voiced":      4,
    "compositing": 5,
    "composited":  5,
    "rendering":   6,
    "rendered":    6,
}

_ACTIVE_STATUSES = {
    "searching", "scripting", "animating", "voicing", "compositing", "rendering",
}


def _derive_statuses(project_status: str | None) -> list[str]:
    if not project_status:
        return ["pending"] * 6

    stage = _STATUS_TO_STAGE.get(project_status, 0)
    is_active = project_status in _ACTIVE_STATUSES
    statuses = []
    for i in range(1, 7):
        if i < stage:
            statuses.append("done")
        elif i == stage:
            statuses.append("running" if is_active else "done")
        else:
            statuses.append("pending")
    return statuses


def _pipeline_html(statuses: list[str]) -> str:
    icons = {"pending": "○", "running": "◉", "done": "✓", "failed": "✕"}
    css_class = {
        "pending": "stage-pending",
        "running": "stage-running",
        "done":    "stage-done",
        "failed":  "stage-failed",
    }

    # Flat structure: card → arrow → card → arrow → …
    parts: list[str] = []
    for i, stage in enumerate(_STAGES):
        st_cls = css_class[statuses[i]]
        icon   = icons[statuses[i]]
        hitl   = '<span class="hitl-badge">HITL</span>' if stage["hitl"] else ""
        parts.append(
            f'<div class="stage-box {st_cls}">{hitl}'
            f'<div class="stage-icon">{icon}</div>'
            f'<div class="stage-num">Stage {stage["num"]}</div>'
            f'<div class="stage-name">{stage["name"]}</div>'
            f'<div class="stage-desc">{stage["desc"]}</div>'
            f'</div>'
        )
        if i < len(_STAGES) - 1:
            arr_cls = "arrow active" if statuses[i] == "done" else "arrow"
            parts.append(f'<div class="{arr_cls}"></div>')

    stage_html = "".join(parts)

    return f"""<style>
.pipeline {{
  display: flex;
  align-items: stretch;
  padding: 20px 8px;
  background: transparent;
  width: 100%;
  box-sizing: border-box;
}}
.stage-box {{
  flex: 1;
  min-height: 130px;
  border-radius: 12px;
  padding: 14px 8px;
  text-align: center;
  position: relative;
  border: 2px solid;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 5px;
}}
.stage-pending {{ background: #111827; border-color: #374151; color: #4b5563; }}
.stage-running {{ background: #1e3a5f; border-color: #3b82f6; color: #93c5fd; animation: pulse-glow 1.8s ease-in-out infinite; }}
.stage-done    {{ background: #052e16; border-color: #16a34a; color: #86efac; }}
.stage-failed  {{ background: #450a0a; border-color: #dc2626; color: #fca5a5; }}
@keyframes pulse-glow {{
  0%, 100% {{ box-shadow: 0 0 0 0 rgba(59,130,246,0.6); }}
  50%       {{ box-shadow: 0 0 0 10px rgba(59,130,246,0); }}
}}
.stage-icon {{ font-size: 20px; line-height: 1; }}
.stage-num  {{ font-size: 9px; text-transform: uppercase; letter-spacing: .08em; opacity: .55; }}
.stage-name {{ font-size: 12px; font-weight: 700; line-height: 1.3; }}
.stage-desc {{ font-size: 9px; opacity: .65; }}
.hitl-badge {{
  position: absolute; top: -9px; right: -7px;
  background: #7c3aed; color: #fff;
  font-size: 8px; font-weight: 700;
  padding: 2px 6px; border-radius: 8px;
  text-transform: uppercase; letter-spacing: .05em;
}}
.arrow {{
  flex: 0 0 32px;
  position: relative;
  align-self: center;
}}
.arrow::before {{
  content: '';
  position: absolute;
  top: 50%; left: 0; right: 8px;
  height: 2px;
  background: #374151;
  transform: translateY(-50%);
}}
.arrow::after {{
  content: '';
  position: absolute;
  right: 0; top: 50%;
  transform: translateY(-50%);
  width: 0; height: 0;
  border-top: 5px solid transparent;
  border-bottom: 5px solid transparent;
  border-left: 8px solid #374151;
}}
.arrow.active::before {{ background: #16a34a; }}
.arrow.active::after  {{ border-left-color: #16a34a; }}
</style>
<div class="pipeline">{stage_html}</div>"""


@st.fragment(run_every=3)
def _live_pipeline() -> None:
    proj = st.session_state.get("current_project")
    if not proj:
        return

    pid = proj.get("project_id", "")

    # Re-read status from storage so we catch updates from background workers
    projects = load_projects()
    fresh = next((p for p in projects if p["project_id"] == pid), None)
    project_status = (fresh or proj).get("status")

    # Sync session state if status changed
    if fresh and fresh.get("status") != proj.get("status"):
        st.session_state["current_project"]["status"] = fresh["status"]

    statuses = _derive_statuses(project_status)
    html = _pipeline_html(statuses)
    html_compact = "\n".join(line for line in html.splitlines() if line.strip())
    st.markdown(html_compact, unsafe_allow_html=True)

    st.markdown("")
    leg_cols = st.columns(4)
    leg_cols[0].markdown("⬜ **Pending**")
    leg_cols[1].markdown("🔵 **Running** (pulsing)")
    leg_cols[2].markdown("🟢 **Done**")
    leg_cols[3].markdown("🔴 **Failed**")

    st.success(
        f"Active project: **{proj.get('topic', '—')}** "
        f"({proj.get('language','').upper()}) — status: **{project_status or '—'}**"
    )


_NEXT_STEP = {
    # status → (stage page label, human instruction)
    "searching":   ("🔍  Topic Search", "Run topic search and pick a topic."),
    "searched":    ("🔍  Topic Search", "Review topic candidates and approve one."),
    "scripting":   ("📝  Script",       "Script generation is running — review it when done."),
    "scripted":    ("📝  Script",       "Review and approve the generated script."),
    "approved":    ("🎬  Scene QA",     "Script approved — start Manim animation and review scenes."),
    "animating":   ("🎬  Scene QA",     "Animation is rendering — review scenes as they finish."),
    "animated":    ("🔊  Voiceover",    "Scenes ready — generate the voiceover."),
    "voicing":     ("🔊  Voiceover",    "Voiceover is being synthesized."),
    "voiced":      ("🔊  Voiceover",    "Voiceover done — run composite and final render."),
    "compositing": ("🔊  Voiceover",    "Compositing clips with narration timing…"),
    "composited":  ("🔊  Voiceover",    "Composite done — run the final render."),
    "rendering":   ("🔊  Voiceover",    "Final render in progress…"),
    "rendered":    ("🔊  Voiceover",    "Done! Preview and download the final video."),
}


def render() -> None:
    st.title("Pipeline Workflow")
    st.caption("Live view of the 6-stage video production pipeline. Auto-refreshes every 3s.")

    proj = st.session_state.get("current_project")
    if not proj:
        st.warning("No active project. Go to **Projects** to create or load one.")
        return

    _live_pipeline()

    # ── Guided next step ──────────────────────────────────────────────────────
    st.divider()
    status = proj.get("status", "searching")
    page_label, instruction = _NEXT_STEP.get(
        status, ("🔍  Topic Search", "Start with a topic search.")
    )

    cta_cols = st.columns([3, 1])
    cta_cols[0].markdown(f"**Next step:** {instruction}")
    if cta_cols[1].button(f"Go to {page_label.split('  ')[-1]} →", type="primary",
                          key="workflow_next_step", use_container_width=True):
        st.session_state["pending_stage_nav"] = page_label
        st.rerun()

    # ── Per-scene status summary (when a spec exists) ─────────────────────────
    spec_dict = st.session_state.get("approved_spec") or st.session_state.get("draft_spec")
    if spec_dict and spec_dict.get("scenes"):
        with st.expander(f"📊 Scene status ({len(spec_dict['scenes'])} scenes)", expanded=False):
            for s in sorted(spec_dict["scenes"], key=lambda x: x.get("order", 0)):
                anim = "✅" if s.get("clip_qa_passed") else ("⚠️" if s.get("clip_path") else "—")
                voice = "✅" if s.get("duration_sec") else "—"
                dur = f"{s['duration_sec']:.0f}s" if s.get("duration_sec") else ""
                st.markdown(
                    f"`{s.get('id','?')}` · {s.get('visual_type','?')} · "
                    f"animation {anim} · voice {voice} {dur} · "
                    f"{len(s.get('beats', []))} beats"
                )
