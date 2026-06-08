"""Stage 5 — Composite pipeline (deterministic, no LLM).

Assembles clips according to duration_sec from voiceover stage.
Raises AssertionError if any scene is missing duration_sec — voiceover MUST run first.
"""

from __future__ import annotations

import os
from pathlib import Path

import structlog
from moviepy import AudioFileClip, CompositeVideoClip, VideoFileClip, concatenate_videoclips

from app.config import get_settings
from app.models.video_spec import VideoSpec

log = structlog.get_logger()


async def run_composite(spec: VideoSpec, artifact_dir: str | None = None) -> str:
    """Assemble all scene clips into a single silent video. Returns output path."""
    cfg = get_settings()
    base_dir = artifact_dir or cfg.artifact_dir

    # Invariant: voiceover must have run for all scenes
    missing = [s.id for s in spec.scenes if s.duration_sec is None]
    assert not missing, f"Scenes missing duration_sec (voiceover not run): {missing}"

    clips = []
    for scene in sorted(spec.scenes, key=lambda s: s.order):
        if scene.clip_path and Path(scene.clip_path).exists():
            clip = VideoFileClip(scene.clip_path)
            # Trim or freeze-extend to match voice duration
            target = scene.duration_sec
            if clip.duration > target:
                clip = clip.subclipped(0, target)
            elif clip.duration < target:
                # Freeze last frame for remainder
                still = clip.to_ImageClip(t=clip.duration - 0.05).with_duration(target - clip.duration)
                clip = concatenate_videoclips([clip, still])
        else:
            log.warning("composite.missing_clip", scene_id=scene.id)
            # Black placeholder
            from moviepy import ColorClip
            clip = ColorClip(size=(1920, 1080), color=[0, 0, 0], duration=scene.duration_sec)

        clips.append(clip)

    final = concatenate_videoclips(clips, method="compose")

    out_path = os.path.join(base_dir, spec.project_id, "composite.mp4")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    log.info("composite.render_start", scenes=len(clips), total_dur=final.duration)
    final.write_videofile(out_path, fps=24, codec="libx264", audio=False, logger=None)
    log.info("composite.render_done", path=out_path)

    for c in clips:
        c.close()
    final.close()

    return out_path
