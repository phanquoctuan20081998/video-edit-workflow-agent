"""Typed agent registry with auto-discovery (inspired by VideoAgent's FunctionRegistry).

Each pipeline stage is a typed agent with declared InputSchema and OutputSchema.
This enables:
  - Compile-time validation of stage connections (inputs match upstream outputs)
  - Auto-generated documentation for HITL review
  - Early "infeasible" detection if no valid graph exists for a topic

Usage:
    from app.agents import registry

    # List all registered agents with their I/O schemas
    registry.list_agents()

    # Validate that stage connections are compatible
    registry.validate_pipeline()

    # Get agent by name
    agent_cls = registry.get("manim_codegen")
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Type

import structlog

log = structlog.get_logger()


class StageOrder(int, Enum):
    """Pipeline stage ordering for validation."""
    MARKET_SEARCH = 1
    SCRIPT = 2
    SPEC_JUDGE = 25  # between script and codegen
    MANIM_CODEGEN = 3
    VOICEOVER = 4
    BEAT_TIMING = 45  # between voiceover and composite
    COMPOSITE = 5
    RENDER = 6


@dataclass
class ParamSchema:
    """Describes a single input or output parameter."""
    name: str
    type_hint: str  # e.g. "VideoSpec", "str", "list[Scene]"
    description: str
    required: bool = True
    default: Any = None


@dataclass
class AgentMeta:
    """Metadata for a registered pipeline agent."""
    name: str
    stage: StageOrder
    description: str
    input_schema: list[ParamSchema] = field(default_factory=list)
    output_schema: list[ParamSchema] = field(default_factory=list)
    agent_class: Type | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "stage": self.stage.name,
            "stage_order": self.stage.value,
            "description": self.description,
            "inputs": [
                {"name": p.name, "type": p.type_hint, "description": p.description, "required": p.required}
                for p in self.input_schema
            ],
            "outputs": [
                {"name": p.name, "type": p.type_hint, "description": p.description}
                for p in self.output_schema
            ],
        }


# ── Registry ──────────────────────────────────────────────────────────────────

_REGISTRY: OrderedDict[str, AgentMeta] = OrderedDict()


def register_agent(
    name: str,
    stage: StageOrder,
    description: str,
    inputs: list[ParamSchema],
    outputs: list[ParamSchema],
):
    """Decorator to register an agent class with typed I/O schema."""
    def decorator(cls):
        meta = AgentMeta(
            name=name,
            stage=stage,
            description=description,
            input_schema=inputs,
            output_schema=outputs,
            agent_class=cls,
        )
        _REGISTRY[name] = meta
        cls._agent_meta = meta
        return cls
    return decorator


def get(name: str) -> AgentMeta:
    """Get registered agent metadata by name."""
    if name not in _REGISTRY:
        raise KeyError(f"Agent '{name}' not registered. Available: {list(_REGISTRY.keys())}")
    return _REGISTRY[name]


def list_agents() -> list[AgentMeta]:
    """List all registered agents in stage order."""
    return sorted(_REGISTRY.values(), key=lambda a: a.stage.value)


def list_agents_dict() -> list[dict]:
    """List all registered agents as dicts (for API/UI)."""
    return [a.to_dict() for a in list_agents()]


def validate_pipeline() -> list[str]:
    """Validate that pipeline stages connect properly (output types match next input types).

    Returns list of validation issues (empty = valid).

    Note: In this pipeline, VideoSpec is the shared backbone — most stages read/write
    fields on the same VideoSpec object rather than passing discrete outputs forward.
    So we only flag gaps where there's no plausible connection via VideoSpec.
    """
    agents = list_agents()
    issues = []

    # VideoSpec is the shared bus — stages that accept or produce it are connected
    videospec_types = {"VideoSpec", "Scene"}  # Scene is a sub-object of VideoSpec

    for i in range(len(agents) - 1):
        current = agents[i]
        next_agent = agents[i + 1]

        current_output_types = {p.type_hint for p in current.output_schema}
        next_required_inputs = {p.type_hint for p in next_agent.input_schema if p.required}

        # Stages are connected if they share types OR both touch VideoSpec/Scene
        direct_overlap = current_output_types & next_required_inputs
        via_spec = (current_output_types & videospec_types) or (next_required_inputs & videospec_types)

        if not direct_overlap and not via_spec and next_required_inputs:
            issues.append(
                f"Stage gap: {current.name} outputs {current_output_types} "
                f"but {next_agent.name} requires {next_required_inputs}"
            )

    return issues


# ── Auto-registration of built-in agents ──────────────────────────────────────
# Each agent module registers itself when imported. We trigger imports here.

def _auto_discover():
    """Import all agent modules to trigger their @register_agent decorators."""
    agents_dir = Path(__file__).parent
    for module_info in pkgutil.iter_modules([str(agents_dir)]):
        if module_info.name.startswith("_") or module_info.name == "registry":
            continue
        try:
            importlib.import_module(f"app.agents.{module_info.name}")
        except ImportError as e:
            log.debug("registry.skip_module", module=module_info.name, error=str(e))


# Register built-in agents with their schemas (inline, no decorator needed on existing classes)

def _register_builtins():
    """Register existing agent classes that predate the decorator pattern."""
    # Market Search Agent
    _REGISTRY["market_search"] = AgentMeta(
        name="market_search",
        stage=StageOrder.MARKET_SEARCH,
        description="Find trending topics scored on trending + Manim visualizability axes",
        input_schema=[
            ParamSchema(name="query", type_hint="str", description="Search domain or seed topic", required=False),
            ParamSchema(name="max_results", type_hint="int", description="Max topics to return", required=False, default=10),
        ],
        output_schema=[
            ParamSchema(name="topics", type_hint="list[TopicCandidate]", description="Ranked topic list with scores"),
        ],
    )

    # Script Agent
    _REGISTRY["script"] = AgentMeta(
        name="script",
        stage=StageOrder.SCRIPT,
        description="Research topic → emit VideoSpec with scenes and beats",
        input_schema=[
            ParamSchema(name="topic", type_hint="str", description="Approved topic to script"),
            ParamSchema(name="language", type_hint="str", description="Target language for narration", required=False, default="en"),
        ],
        output_schema=[
            ParamSchema(name="spec", type_hint="VideoSpec", description="Complete VideoSpec with scenes and beats"),
        ],
    )

    # Spec Judge
    _REGISTRY["spec_judge"] = AgentMeta(
        name="spec_judge",
        stage=StageOrder.SPEC_JUDGE,
        description="Validate VideoSpec structure + Manim feasibility before codegen",
        input_schema=[
            ParamSchema(name="spec", type_hint="VideoSpec", description="VideoSpec to validate"),
        ],
        output_schema=[
            ParamSchema(name="result", type_hint="JudgeResult", description="Validation result with issues and reflection"),
        ],
    )

    # Manim Codegen
    _REGISTRY["manim_codegen"] = AgentMeta(
        name="manim_codegen",
        stage=StageOrder.MANIM_CODEGEN,
        description="Generate Manim code per scene, sandbox exec, self-repair loop, visual QA",
        input_schema=[
            ParamSchema(name="scene", type_hint="Scene", description="Scene with visual_spec and beats"),
            ParamSchema(name="spec", type_hint="VideoSpec", description="Parent VideoSpec for context"),
        ],
        output_schema=[
            ParamSchema(name="result", type_hint="RenderResult", description="Clip path, QA status, code"),
        ],
    )

    # Voiceover
    _REGISTRY["voiceover"] = AgentMeta(
        name="voiceover",
        stage=StageOrder.VOICEOVER,
        description="TTS each scene → audio_path, duration_sec, word_timestamps",
        input_schema=[
            ParamSchema(name="scene", type_hint="Scene", description="Scene with narration text"),
            ParamSchema(name="language", type_hint="str", description="Language for TTS"),
        ],
        output_schema=[
            ParamSchema(name="audio_path", type_hint="str", description="Path to generated audio"),
            ParamSchema(name="duration_sec", type_hint="float", description="Audio duration (drives timeline)"),
            ParamSchema(name="word_timestamps", type_hint="list[WordTimestamp]", description="Word-level timing"),
        ],
    )

    # Beat Timing Resolver
    _REGISTRY["beat_timing"] = AgentMeta(
        name="beat_timing",
        stage=StageOrder.BEAT_TIMING,
        description="Resolve beat start/duration from word_timestamps + trigger_phrases",
        input_schema=[
            ParamSchema(name="scene", type_hint="Scene", description="Scene with word_timestamps and beats"),
        ],
        output_schema=[
            ParamSchema(name="scene", type_hint="Scene", description="Scene with beats[].start_sec/duration_sec filled"),
        ],
    )

    # Composite
    _REGISTRY["composite"] = AgentMeta(
        name="composite",
        stage=StageOrder.COMPOSITE,
        description="Assemble Manim clips + audio on timeline with subtitles",
        input_schema=[
            ParamSchema(name="spec", type_hint="VideoSpec", description="Fully voiced + animated VideoSpec"),
        ],
        output_schema=[
            ParamSchema(name="video_path", type_hint="str", description="Composited video (no final encode)"),
        ],
    )

    # Render
    _REGISTRY["render"] = AgentMeta(
        name="render",
        stage=StageOrder.RENDER,
        description="Mux video + audio + subtitles + BGM → final encode",
        input_schema=[
            ParamSchema(name="spec", type_hint="VideoSpec", description="Composited VideoSpec"),
        ],
        output_schema=[
            ParamSchema(name="final_video_path", type_hint="str", description="Final rendered video path"),
        ],
    )


_register_builtins()
