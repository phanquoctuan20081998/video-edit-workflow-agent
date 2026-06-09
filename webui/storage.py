"""JSON-based persistence for WebUI history.

Stores topic search runs, generated scripts, scene renders, and project metadata
in data/ directory as JSON files. No DB dependency for the UI layer.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

DATA_DIR = Path(__file__).parent.parent / "data"


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Topic search history ──────────────────────────────────────────────────────

def save_topic_search(prompt: str, candidates: list[dict]) -> str:
    run_id = f"{_ts()}_{str(uuid4())[:8]}"
    _write(
        DATA_DIR / "topic_searches" / f"{run_id}.json",
        {
            "run_id": run_id,
            "timestamp": _now_iso(),
            "prompt": prompt,
            "candidates": candidates,
        },
    )
    return run_id


def load_topic_searches() -> list[dict]:
    search_dir = DATA_DIR / "topic_searches"
    if not search_dir.exists():
        return []
    runs = []
    for f in sorted(search_dir.glob("*.json"), reverse=True):
        data = _read(f)
        if data:
            runs.append(data)
    return runs


# ── Script history ────────────────────────────────────────────────────────────

def save_script(project_id: str, topic: str, language: str, spec_dict: dict) -> str:
    script_id = f"{_ts()}_{str(uuid4())[:8]}"
    _write(
        DATA_DIR / "scripts" / f"{script_id}.json",
        {
            "script_id": script_id,
            "project_id": project_id,
            "topic": topic,
            "language": language,
            "timestamp": _now_iso(),
            "spec": spec_dict,
        },
    )
    return script_id


def load_scripts() -> list[dict]:
    script_dir = DATA_DIR / "scripts"
    if not script_dir.exists():
        return []
    scripts = []
    for f in sorted(script_dir.glob("*.json"), reverse=True):
        data = _read(f)
        if data:
            summary = {k: v for k, v in data.items() if k != "spec"}
            summary["scene_count"] = len(data.get("spec", {}).get("scenes", []))
            scripts.append(summary)
    return scripts


def load_script(script_id: str) -> dict | None:
    return _read(DATA_DIR / "scripts" / f"{script_id}.json")


# ── Scene render history ──────────────────────────────────────────────────────

def save_scene_render(project_id: str, scene_id: str, scene_dict: dict) -> str:
    render_id = f"{_ts()}_{str(uuid4())[:8]}"
    _write(
        DATA_DIR / "scene_renders" / project_id / scene_id / f"{render_id}.json",
        {
            "render_id": render_id,
            "project_id": project_id,
            "scene_id": scene_id,
            "timestamp": _now_iso(),
            "scene": scene_dict,
        },
    )
    return render_id


def load_scene_renders(project_id: str, scene_id: str) -> list[dict]:
    render_dir = DATA_DIR / "scene_renders" / project_id / scene_id
    if not render_dir.exists():
        return []
    renders = []
    for f in sorted(render_dir.glob("*.json"), reverse=True):
        data = _read(f)
        if data:
            renders.append(data)
    return renders


# ── Projects ──────────────────────────────────────────────────────────────────

def save_project(
    project_id: str,
    topic: str,
    language: str,
    status: str,
    spec_dict: dict | None = None,
) -> None:
    projects = _load_projects_raw()
    existing = next((p for p in projects if p["project_id"] == project_id), None)
    if existing:
        existing["status"] = status
        existing["updated_at"] = _now_iso()
        if spec_dict is not None:
            existing["scene_count"] = len(spec_dict.get("scenes", []))
    else:
        projects.append({
            "project_id": project_id,
            "topic": topic,
            "language": language,
            "status": status,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "scene_count": len(spec_dict.get("scenes", [])) if spec_dict else 0,
        })
    _write(DATA_DIR / "projects.json", projects)


def _load_projects_raw() -> list[dict]:
    return _read(DATA_DIR / "projects.json") or []


def load_projects() -> list[dict]:
    return sorted(_load_projects_raw(), key=lambda p: p.get("updated_at", ""), reverse=True)


def load_project_spec(project_id: str) -> dict | None:
    """Return the most recent script spec for a project."""
    script_dir = DATA_DIR / "scripts"
    if not script_dir.exists():
        return None
    for f in sorted(script_dir.glob("*.json"), reverse=True):
        data = _read(f)
        if data and data.get("project_id") == project_id:
            return data.get("spec")
    return None
