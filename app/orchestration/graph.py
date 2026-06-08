"""LangGraph orchestration graph for the full pipeline.

Nodes: market_search → [HITL: topic_approval] → script → [HITL: script_approval]
       → manim_codegen → voiceover → composite → render

HITL interrupts pause the graph at checkpoints until the user approves via the API.
Heavy media nodes (manim_codegen, final render) dispatch to Celery workers.
"""

from __future__ import annotations

import json
from typing import Optional

import structlog
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver
from typing_extensions import TypedDict

log = structlog.get_logger()


class ProjectState(TypedDict):
    project_id: str
    topic: str
    language: str
    spec: Optional[dict]           # VideoSpec as dict
    stage: str
    hitl_feedback: Optional[str]
    error: Optional[str]
    final_video_path: Optional[str]
    _candidates: list              # topic candidates from market_search, for HITL UI
    _composite_path: str           # set by composite node, read by render node


# ── Node functions ─────────────────────────────────────────────────────────────

async def node_market_search(state: ProjectState) -> ProjectState:
    from app.agents.market_search import MarketSearchAgent
    agent = MarketSearchAgent()
    candidates = await agent.search(n_topics=10)
    # Candidates stored in state for HITL page to read
    state["stage"] = "searched"
    state["hitl_feedback"] = None
    # Store serialized candidates for the UI
    state["_candidates"] = [
        {"title": c.title, "score": c.composite_score, "approach": c.approach}
        for c in candidates
    ]
    log.info("graph.market_search_done", n=len(candidates))
    return state


async def node_topic_approval(state: ProjectState) -> ProjectState:
    """HITL checkpoint — resumes after user sets hitl_feedback to chosen topic title."""
    feedback = state.get("hitl_feedback")
    if feedback:
        state["topic"] = feedback
        state["hitl_feedback"] = None
        log.info("graph.topic_approved", topic=state["topic"])
    else:
        log.warning("graph.topic_approval_no_feedback")
    return state


async def node_script(state: ProjectState) -> ProjectState:
    from app.agents.script import ScriptAgent
    agent = ScriptAgent()
    spec = await agent.run(topic=state["topic"], language=state.get("language", "vi"))
    state["spec"] = spec.model_dump()
    state["stage"] = "scripted"
    log.info("graph.script_done", project_id=spec.project_id)
    return state


async def node_script_approval(state: ProjectState) -> ProjectState:
    """HITL checkpoint — resumes after user sets hitl_feedback to updated spec JSON or 'approved'."""
    feedback = state.get("hitl_feedback")
    if feedback and feedback != "approved":
        try:
            state["spec"] = json.loads(feedback)
            log.info("graph.script_approved_with_edits")
        except (json.JSONDecodeError, TypeError):
            log.warning("graph.script_approval_bad_feedback", feedback=feedback[:100])
    else:
        log.info("graph.script_approved")
    state["hitl_feedback"] = None
    return state


async def node_manim_codegen(state: ProjectState) -> ProjectState:
    from app.models.video_spec import VideoSpec
    from app.agents.manim_codegen import run_manim_codegen
    from app.config import get_settings

    spec = VideoSpec.model_validate(state["spec"])
    cfg = get_settings()
    spec = await run_manim_codegen(spec, artifact_dir=cfg.artifact_dir)
    state["spec"] = spec.model_dump()
    state["stage"] = "animated"
    log.info("graph.manim_codegen_done")
    return state


async def node_voiceover(state: ProjectState) -> ProjectState:
    from app.models.video_spec import VideoSpec, ProjectStatus
    from app.pipeline.voiceover import run_voiceover
    from app.config import get_settings

    spec = VideoSpec.model_validate(state["spec"])
    cfg = get_settings()
    spec = await run_voiceover(spec, artifact_dir=cfg.artifact_dir)
    spec.status = ProjectStatus.voiced
    state["spec"] = spec.model_dump()
    state["stage"] = "voiced"
    return state


async def node_composite(state: ProjectState) -> ProjectState:
    from app.models.video_spec import VideoSpec, ProjectStatus
    from app.pipeline.composite import run_composite
    from app.config import get_settings

    spec = VideoSpec.model_validate(state["spec"])
    cfg = get_settings()
    composite_path = await run_composite(spec, artifact_dir=cfg.artifact_dir)
    spec.status = ProjectStatus.composited
    state["spec"] = spec.model_dump()
    state["stage"] = "composited"
    state["_composite_path"] = composite_path
    return state


async def node_render(state: ProjectState) -> ProjectState:
    from app.models.video_spec import VideoSpec, ProjectStatus
    from app.pipeline.render import run_render
    from app.config import get_settings

    spec = VideoSpec.model_validate(state["spec"])
    cfg = get_settings()
    composite_path = state.get("_composite_path") or ""
    if not composite_path:
        raise ValueError("composite_path missing from state — composite stage must run first")
    final_path = await run_render(
        spec,
        composite_path=composite_path,
        artifact_dir=cfg.artifact_dir,
    )
    spec.final_video_path = final_path
    spec.status = ProjectStatus.rendered
    state["spec"] = spec.model_dump()
    state["stage"] = "rendered"
    state["final_video_path"] = final_path
    log.info("graph.render_done", path=final_path)
    return state


# ── Build graph ────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    builder = StateGraph(ProjectState)

    builder.add_node("market_search", node_market_search)
    builder.add_node("topic_approval", node_topic_approval)
    builder.add_node("script", node_script)
    builder.add_node("script_approval", node_script_approval)
    builder.add_node("manim_codegen", node_manim_codegen)
    builder.add_node("voiceover", node_voiceover)
    builder.add_node("composite", node_composite)
    builder.add_node("render", node_render)

    builder.set_entry_point("market_search")
    builder.add_edge("market_search", "topic_approval")
    builder.add_edge("topic_approval", "script")
    builder.add_edge("script", "script_approval")
    builder.add_edge("script_approval", "manim_codegen")
    builder.add_edge("manim_codegen", "voiceover")
    builder.add_edge("voiceover", "composite")
    builder.add_edge("composite", "render")
    builder.add_edge("render", END)

    return builder


def compile_graph(checkpointer=None):
    builder = build_graph()
    checkpointer = checkpointer or MemorySaver()
    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["topic_approval", "script_approval"],
    )


# ── CLI entrypoint ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    import sys

    topic = sys.argv[1] if len(sys.argv) > 1 else "Fast Fourier Transform"

    async def main():
        graph = compile_graph()
        config = {"configurable": {"thread_id": "test-run-1"}}
        init_state: ProjectState = {
            "project_id": "test-1",
            "topic": topic,
            "language": "vi",
            "spec": None,
            "stage": "init",
            "hitl_feedback": None,
            "error": None,
            "final_video_path": None,
            "_candidates": [],
            "_composite_path": "",
        }

        print(f"Starting pipeline for topic: {topic}")
        async for event in graph.astream(init_state, config=config):
            node = list(event.keys())[0]
            state = event[node]
            print(f"  [{node}] stage={state.get('stage')}")

    asyncio.run(main())
