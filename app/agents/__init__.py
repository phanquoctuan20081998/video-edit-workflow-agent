"""Pipeline agents — market search, script, manim codegen, spec judge.

The registry module provides typed I/O schemas for all agents, enabling
compile-time validation of pipeline connections and auto-generated docs.
"""

from app.agents.registry import (
    get,
    list_agents,
    list_agents_dict,
    register_agent,
    validate_pipeline,
)

__all__ = [
    "get",
    "list_agents",
    "list_agents_dict",
    "register_agent",
    "validate_pipeline",
]
