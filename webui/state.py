"""Persistence helpers for Streamlit session state."""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.config import get_settings
from app.models.db import create_tables, get_session_factory
from app.models.project import Project
from app.models.video_spec import VideoSpec


def save_spec(spec: VideoSpec) -> None:
    asyncio.run(_save_spec(spec))


def delete_project(project_id: str) -> None:
    asyncio.run(_delete_project(project_id))


def load_latest_spec() -> VideoSpec | None:
    return asyncio.run(_load_latest_spec())


def hydrate_session_state(session_state) -> None:
    # Project already selected this session — never overwrite with DB data.
    if session_state.get("current_project"):
        return
    # Already hydrated without a project (e.g. first run, no DB yet).
    if session_state.get("draft_spec") or session_state.get("approved_spec"):
        return

    spec = load_latest_spec()
    if spec is None:
        return

    spec_dict = spec.model_dump()
    session_state["draft_spec"] = spec_dict
    session_state["approved_topic"] = spec.topic
    session_state["language"] = spec.language
    # Restore current_project so sidebar shows the right project after F5.
    session_state["current_project"] = {
        "project_id": spec.project_id,
        "topic": spec.topic,
        "language": spec.language,
        "status": spec.status.value,
    }
    if spec.status.value in {"approved", "animated", "voiced", "composited", "rendered"}:
        session_state["approved_spec"] = spec_dict


async def _delete_project(project_id: str) -> None:
    database_url = get_settings().database_url
    await create_tables(database_url)
    session_factory = get_session_factory(database_url)
    async with session_factory() as session:
        project = await session.get(Project, project_id)
        if project:
            await session.delete(project)
            await session.commit()


async def _save_spec(spec: VideoSpec) -> None:
    database_url = get_settings().database_url
    await create_tables(database_url)
    session_factory = get_session_factory(database_url)
    async with session_factory() as session:
        project = await session.get(Project, spec.project_id)
        if project is None:
            project = Project(project_id=spec.project_id, topic=spec.topic)
            session.add(project)
        project.topic = spec.topic
        project.set_spec(spec)
        await session.commit()


async def _load_latest_spec() -> VideoSpec | None:
    database_url = get_settings().database_url
    await create_tables(database_url)
    session_factory = get_session_factory(database_url)
    async with session_factory() as session:
        result = await session.execute(select(Project).order_by(Project.updated_at.desc()).limit(20))
        for project in result.scalars():
            if not project.spec_json:
                continue
            spec = project.get_spec()
            if spec.scenes:
                return spec
        return None
