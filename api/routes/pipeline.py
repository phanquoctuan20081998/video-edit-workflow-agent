"""Pipeline trigger endpoints — start stages, get status."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


async def get_session():
    from app.config import get_settings
    from app.models.db import get_session_factory
    sf = get_session_factory(get_settings().database_url)
    async with sf() as session:
        yield session


@router.post("/{project_id}/start")
async def start_pipeline(project_id: str, session: AsyncSession = Depends(get_session)):
    """Trigger full pipeline via Celery (market search + script must already be done)."""
    from app.models.project import Project
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    from app.orchestration.tasks import task_render_final
    task = task_render_final.delay(project_id)
    return {"project_id": project_id, "task_id": task.id, "status": "queued"}


@router.post("/{project_id}/render_scene/{scene_id}")
async def render_scene(
    project_id: str,
    scene_id: str,
    max_repairs: int = 4,
    session: AsyncSession = Depends(get_session),
):
    """Trigger single-scene Manim render via Celery."""
    from app.models.project import Project
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    from app.orchestration.tasks import task_render_scene
    task = task_render_scene.delay(project_id, scene_id, max_repairs)
    return {"project_id": project_id, "scene_id": scene_id, "task_id": task.id, "status": "queued"}


@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """Poll Celery task status."""
    from app.orchestration.celery_app import celery_app
    result = celery_app.AsyncResult(task_id)
    return {
        "task_id": task_id,
        "status": result.status,
        "result": result.result if result.ready() else None,
    }
