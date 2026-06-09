"""Workflow page — animated pipeline visualization."""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components


_STAGES = [
    {"num": 1, "name": "Market Search", "desc": "Find trending topics", "hitl": True,  "key": "topic"},
    {"num": 2, "name": "Script",        "desc": "Research & write script", "hitl": True,  "key": "script"},
    {"num": 3, "name": "Manim Codegen", "desc": "Generate animations",   "hitl": False, "key": "manim"},
    {"num": 4, "name": "Voiceover",     "desc": "TTS + timing",           "hitl": False, "key": "voice"},
    {"num": 5, "name": "Composite",     "desc": "Assemble clips",         "hitl": False, "key": "composite"},
    {"num": 6, "name": "Render",        "desc": "Final encode",           "hitl": False, "key": "render"},
]

_STATUS_ORDER = ["searched", "scripted", "approved", "animated", "voiced", "composited", "rendered"]

_STATUS_TO_STAGE = {
    "searched":   1,
    "scripted":   2,
    "approved":   2,
    "animated":   3,
    "voiced":     4,
    "composited": 5,
    "rendered":   6,
}


def _derive_statuses(project_status: str | None) -> list[str]:
    """Return per-stage status strings based on current project status."""
    if not project_status:
        return ["pending"] * 6

    active_idx = _STATUS_TO_STAGE.get(project_status, 0)
    statuses = []
    for i in range(1, 7):
        if i < active_idx:
            statuses.append("done")
        elif i == active_idx:
            statuses.append("running")
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

    stage_html = ""
    for i, stage in enumerate(_STAGES):
        st_cls = css_class[statuses[i]]
        icon   = icons[statuses[i]]
        hitl   = '<span class="hitl-badge">HITL</span>' if stage["hitl"] else ""
        arrow  = ""
        if i < len(_STAGES) - 1:
            arr_cls = "arrow-active" if statuses[i] == "done" else "arrow"
            arrow = f'<div class="{arr_cls}">&#8594;</div>'

        stage_html += f"""
        <div class="stage-wrapper">
          <div class="stage-box {st_cls}">
            {hitl}
            <div class="stage-icon">{icon}</div>
            <div class="stage-num">Stage {stage["num"]}</div>
            <div class="stage-name">{stage["name"]}</div>
            <div class="stage-desc">{stage["desc"]}</div>
          </div>
          {arrow}
        </div>
        """

    return f"""
    <style>
      * {{ box-sizing: border-box; margin: 0; padding: 0; }}
      body {{ background: transparent; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
      .pipeline {{
        display: flex;
        align-items: center;
        gap: 0;
        padding: 24px 16px;
        overflow-x: auto;
        background: #0e1117;
        border-radius: 14px;
        border: 1px solid #1f2937;
      }}
      .stage-wrapper {{ display: flex; align-items: center; }}
      .stage-box {{
        width: 140px;
        min-height: 120px;
        border-radius: 12px;
        padding: 14px 12px;
        text-align: center;
        position: relative;
        border: 2px solid;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 5px;
        flex-shrink: 0;
      }}
      .stage-pending {{
        background: #111827;
        border-color: #374151;
        color: #4b5563;
      }}
      .stage-running {{
        background: #1e3a5f;
        border-color: #3b82f6;
        color: #93c5fd;
        animation: pulse-glow 1.8s ease-in-out infinite;
      }}
      .stage-done {{
        background: #052e16;
        border-color: #16a34a;
        color: #86efac;
      }}
      .stage-failed {{
        background: #450a0a;
        border-color: #dc2626;
        color: #fca5a5;
      }}
      @keyframes pulse-glow {{
        0%, 100% {{ box-shadow: 0 0 0 0 rgba(59,130,246,0.6); }}
        50%       {{ box-shadow: 0 0 0 10px rgba(59,130,246,0); }}
      }}
      .stage-icon  {{ font-size: 22px; line-height: 1; }}
      .stage-num   {{ font-size: 10px; text-transform: uppercase; letter-spacing: .08em; opacity: .6; }}
      .stage-name  {{ font-size: 13px; font-weight: 700; line-height: 1.3; }}
      .stage-desc  {{ font-size: 10px; opacity: .65; }}
      .hitl-badge {{
        position: absolute; top: -10px; right: -8px;
        background: #7c3aed; color: #fff;
        font-size: 9px; font-weight: 700;
        padding: 2px 7px; border-radius: 10px;
        text-transform: uppercase; letter-spacing: .06em;
      }}
      .arrow       {{ color: #374151; font-size: 22px; margin: 0 10px; flex-shrink: 0; }}
      .arrow-active {{ color: #16a34a; font-size: 22px; margin: 0 10px; flex-shrink: 0; }}
    </style>
    <div class="pipeline">{stage_html}</div>
    """


def render() -> None:
    st.title("Pipeline Workflow")
    st.caption("Live view of the 6-stage video production pipeline.")

    proj = st.session_state.get("current_project")
    project_status = proj.get("status") if proj else None

    statuses = _derive_statuses(project_status)
    components.html(_pipeline_html(statuses), height=200, scrolling=False)

    # ── Status legend ─────────────────────────────────────────────────────────
    st.markdown("")
    leg_cols = st.columns(4)
    leg_cols[0].markdown("⬜ **Pending**")
    leg_cols[1].markdown("🔵 **Running** (pulsing)")
    leg_cols[2].markdown("🟢 **Done**")
    leg_cols[3].markdown("🔴 **Failed**")

    st.divider()

    # ── Per-stage detail cards ────────────────────────────────────────────────
    st.subheader("Stage Details")
    cols = st.columns(3)
    for i, (stage, status) in enumerate(zip(_STAGES, statuses)):
        col = cols[i % 3]
        status_emoji = {"pending": "⏳", "running": "⚡", "done": "✅", "failed": "❌"}.get(status, "—")
        hitl_tag = " · **HITL**" if stage["hitl"] else ""
        with col:
            st.markdown(f"""
**Stage {stage['num']} — {stage['name']}** {status_emoji}
{stage['desc']}{hitl_tag}
""")

    st.divider()

    # ── Quick navigation ──────────────────────────────────────────────────────
    st.subheader("Quick Navigate")
    nav_cols = st.columns(3)
    nav_cols[0].info("**Topic Search** → Stage 1  \nRun market search, view history, pick a topic.")
    nav_cols[1].info("**Script** → Stage 2  \nGenerate & edit VideoSpec with scenes and beats.")
    nav_cols[2].info("**Scene QA** → Stage 3  \nRun Manim codegen, review renders, pick best variant.")

    if not proj:
        st.warning("No active project. Go to **Projects** to create or load one.")
    else:
        st.success(
            f"Active project: **{proj.get('topic', '—')}** "
            f"({proj.get('language','').upper()}) — status: **{project_status or '—'}**"
        )
