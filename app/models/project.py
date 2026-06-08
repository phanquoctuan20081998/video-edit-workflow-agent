"""SQLAlchemy ORM model for Project state."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .video_spec import ProjectStatus, VideoSpec


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    topic: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default=ProjectStatus.scripted.value)
    spec_json: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def get_spec(self) -> VideoSpec:
        return VideoSpec.model_validate_json(self.spec_json)

    def set_spec(self, spec: VideoSpec) -> None:
        self.spec_json = spec.model_dump_json()
        self.status = spec.status.value
