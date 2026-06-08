"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import hitl, pipeline, projects

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.config import get_settings
    from app.models.db import create_tables

    cfg = get_settings()
    await create_tables(cfg.database_url)
    log.info("api.startup", db=cfg.database_url)
    yield
    log.info("api.shutdown")


app = FastAPI(
    title="Video Edit Workflow Agent",
    description="API for the explainer video generation pipeline",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501"],  # Streamlit
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(pipeline.router)
app.include_router(hitl.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
