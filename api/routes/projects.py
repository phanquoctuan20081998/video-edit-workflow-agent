"""Project CRUD endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import get_session_factory
from app.models.project import Project
from app.models.video_spec import VideoSpec

router = APIRouter(prefix="/projects", tags=["projects"])


async def get_session():
    from app.config import get_settings
    sf = get_session_factory(get_settings().database_url)
    async with sf() as session:
        yield session


@router.get("/")
async def list_projects(session: AsyncSession = Depends(get_session)):
    from sqlalchemy import select
    result = await session.execute(select(Project).order_by(Project.created_at.desc()))
    projects = result.scalars().all()
    return [
        {"project_id": p.project_id, "topic": p.topic, "status": p.status, "created_at": p.created_at}
        for p in projects
    ]


@router.get("/{project_id}")
async def get_project(project_id: str, session: AsyncSession = Depends(get_session)):
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"project_id": project.project_id, "topic": project.topic, "status": project.status, "spec": project.spec_json}


@router.post("/")
async def create_project(topic: str, language: str = "vi", session: AsyncSession = Depends(get_session)):
    from datetime import datetime, timezone
    spec = VideoSpec(topic=topic, language=language)
    project = Project(
        project_id=spec.project_id,
        topic=topic,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    project.set_spec(spec)
    session.add(project)
    await session.commit()
    return {"project_id": project.project_id, "topic": topic, "status": project.status}


@router.delete("/{project_id}")
async def delete_project(project_id: str, session: AsyncSession = Depends(get_session)):
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    await session.delete(project)
    await session.commit()
    return {"deleted": project_id}
