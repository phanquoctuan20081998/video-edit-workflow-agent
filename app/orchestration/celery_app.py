"""Celery application instance."""

from celery import Celery

from app.config import get_settings

cfg = get_settings()

celery_app = Celery(
    "video_agent",
    broker=cfg.celery_broker_url,
    backend=cfg.celery_result_backend,
    include=["app.orchestration.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,   # one job at a time per worker (render jobs are heavy)
)
