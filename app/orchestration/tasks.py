"""Celery tasks for heavy media jobs (Manim render, final composite+render).

These run in a separate worker process so the main web process stays responsive.
LangGraph orchestration triggers these tasks and polls for completion.
"""

from __future__ import annotations

import asyncio

import structlog

from app.orchestration.celery_app import celery_app

log = structlog.get_logger()


@celery_app.task(bind=True, name="tasks.render_scene", max_retries=2, default_retry_delay=30)
def task_render_scene(self, project_id: str, scene_id: str, max_repairs: int = 4):
    """Render a single Manim scene. Updates VideoSpec in DB on completion."""
    try:
        return asyncio.run(_render_scene_async(project_id, scene_id, max_repairs))
    except Exception as exc:
        log.error("task.render_scene.failed", project_id=project_id, scene_id=scene_id, error=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(bind=True, name="tasks.render_final", max_retries=1, default_retry_delay=60)
def task_render_final(self, project_id: str):
    """Run voiceover → composite → render for a project."""
    try:
        return asyncio.run(_render_final_async(project_id))
    except Exception as exc:
        log.error("task.render_final.failed", project_id=project_id, error=str(exc))
        raise self.retry(exc=exc)


async def _render_scene_async(project_id: str, scene_id: str, max_repairs: int) -> dict:
    from app.config import get_settings
    from app.models.db import create_tables, get_session_factory
    from app.models.project import Project
    from app.agents.manim_codegen import render_scene

    cfg = get_settings()
    await create_tables(cfg.database_url)
    sf = get_session_factory(cfg.database_url)

    async with sf() as session:
        project = await session.get(Project, project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")
        spec = project.get_spec()
        scene = spec.get_scene(scene_id)
        result = await render_scene(scene, spec, artifact_dir=cfg.artifact_dir, max_repairs=max_repairs)
        project.set_spec(spec)
        await session.commit()

    return {
        "success": result.success,
        "clip_path": result.clip_path,
        "qa_passed": result.qa_passed,
        "attempts": result.attempts,
    }


async def _render_final_async(project_id: str) -> dict:
    from app.config import get_settings
    from app.models.db import create_tables, get_session_factory
    from app.models.project import Project
    from app.models.video_spec import ProjectStatus
    from app.pipeline.voiceover import run_voiceover
    from app.pipeline.composite import run_composite
    from app.pipeline.render import run_render

    cfg = get_settings()
    await create_tables(cfg.database_url)
    sf = get_session_factory(cfg.database_url)

    async with sf() as session:
        project = await session.get(Project, project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        spec = project.get_spec()

        # Stage 4 — voiceover
        spec = await run_voiceover(spec, artifact_dir=cfg.artifact_dir)
        spec.status = ProjectStatus.voiced
        project.set_spec(spec)
        await session.commit()

        # Stage 5 — composite
        composite_path = await run_composite(spec, artifact_dir=cfg.artifact_dir)
        spec.status = ProjectStatus.composited
        project.set_spec(spec)
        await session.commit()

        # Stage 6 — render
        final_path = await run_render(spec, composite_path=composite_path, artifact_dir=cfg.artifact_dir)
        spec.final_video_path = final_path
        spec.status = ProjectStatus.rendered
        project.set_spec(spec)
        await session.commit()

    return {"final_video_path": final_path}
