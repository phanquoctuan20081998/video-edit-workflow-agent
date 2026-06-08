"""HITL endpoints — approve/reject at pipeline checkpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/hitl", tags=["hitl"])


async def get_session():
    from app.config import get_settings
    from app.models.db import get_session_factory
    sf = get_session_factory(get_settings().database_url)
    async with sf() as session:
        yield session


class TopicApproval(BaseModel):
    approved_topic: str
    language: str = "vi"


class ScriptApproval(BaseModel):
    spec_json: str   # Full updated VideoSpec JSON


@router.post("/{project_id}/approve_topic")
async def approve_topic(
    project_id: str,
    body: TopicApproval,
    session: AsyncSession = Depends(get_session),
):
    from app.models.project import Project
    from app.models.video_spec import VideoSpec

    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Update topic on the project
    project.topic = body.approved_topic
    if project.spec_json:
        spec = project.get_spec()
        spec.topic = body.approved_topic
        spec.language = body.language
        project.set_spec(spec)

    await session.commit()
    return {"project_id": project_id, "approved_topic": body.approved_topic}


@router.post("/{project_id}/approve_script")
async def approve_script(
    project_id: str,
    body: ScriptApproval,
    session: AsyncSession = Depends(get_session),
):
    from app.models.project import Project
    from app.models.video_spec import VideoSpec, ProjectStatus

    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    spec = VideoSpec.model_validate_json(body.spec_json)
    spec.status = ProjectStatus.approved
    project.set_spec(spec)
    await session.commit()
    return {"project_id": project_id, "status": "approved", "scenes": len(spec.scenes)}


@router.post("/{project_id}/approve_scene/{scene_id}")
async def approve_scene(
    project_id: str,
    scene_id: str,
    session: AsyncSession = Depends(get_session),
):
    from app.models.project import Project

    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    spec = project.get_spec()
    scene = spec.get_scene(scene_id)
    scene.clip_qa_passed = True   # Manual human override
    project.set_spec(spec)
    await session.commit()
    return {"project_id": project_id, "scene_id": scene_id, "approved": True}
