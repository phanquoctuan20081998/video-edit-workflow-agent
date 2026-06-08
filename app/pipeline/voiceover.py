"""Stage 4 — Voiceover pipeline.

Calls TTS provider per scene, fills:
  scene.audio_path, scene.duration_sec, scene.word_timestamps

duration_sec becomes the source of truth for the timeline in Stage 5.
MUST run before composite. Raises if called after timeline is finalized.
"""

from __future__ import annotations

import os
from pathlib import Path

import structlog

from app.config import get_settings
from app.models.video_spec import Scene, VideoSpec, WordTimestamp
from app.providers.factory import get_tts_provider

log = structlog.get_logger()


async def run_voiceover(spec: VideoSpec, artifact_dir: str | None = None) -> VideoSpec:
    """Synthesize TTS for all scenes. Returns updated spec."""
    cfg = get_settings()
    base_dir = artifact_dir or cfg.artifact_dir
    tts = get_tts_provider()

    for scene in spec.scenes:
        if scene.audio_path and scene.duration_sec is not None:
            log.info("voiceover.skip_cached", scene_id=scene.id)
            continue

        out_path = os.path.join(base_dir, spec.project_id, "audio", f"{scene.id}.mp3")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)

        log.info("voiceover.synthesize", scene_id=scene.id, chars=len(scene.narration))
        result = await tts.synthesize(scene.narration, out_path)

        scene.set_voiceover(
            audio_path=result.audio_path,
            duration_sec=result.duration_sec,
            word_timestamps=[
                WordTimestamp(word=w["word"], start=w["start"], end=w["end"])
                for w in result.word_timestamps
            ],
        )
        log.info("voiceover.done", scene_id=scene.id, duration=result.duration_sec)

    return spec
