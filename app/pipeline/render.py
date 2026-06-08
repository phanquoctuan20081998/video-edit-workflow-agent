"""Stage 6 — Final render (deterministic, no LLM).

Muxes video + per-scene audio + BGM + optional subtitles → final H.264 mp4.
Uses ffmpeg-python. No LLM involvement.
"""

from __future__ import annotations

import os
from pathlib import Path

import ffmpeg
import structlog

from app.config import get_settings
from app.models.video_spec import VideoSpec

log = structlog.get_logger()


async def run_render(
    spec: VideoSpec,
    composite_path: str,
    artifact_dir: str | None = None,
) -> str:
    """Mux composite video with audio tracks and BGM. Returns final video path."""
    cfg = get_settings()
    base_dir = artifact_dir or cfg.artifact_dir
    out_path = os.path.join(base_dir, spec.project_id, "final.mp4")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    # Concatenate per-scene audio into a single track
    audio_concat_path = os.path.join(base_dir, spec.project_id, "audio_concat.mp3")
    _concat_audio(spec, audio_concat_path)

    video_in = ffmpeg.input(composite_path)
    audio_in = ffmpeg.input(audio_concat_path)
    inputs = [video_in, audio_in]
    audio_streams = [audio_in.audio]

    if spec.bgm.path and Path(spec.bgm.path).exists():
        bgm_in = ffmpeg.input(spec.bgm.path, stream_loop=-1, t=_total_duration(spec))
        bgm_vol = bgm_in.audio.filter("volume", spec.bgm.volume)
        audio_streams.append(bgm_vol)

    if len(audio_streams) > 1:
        mixed_audio = ffmpeg.filter(audio_streams, "amix", inputs=len(audio_streams), duration="first")
    else:
        mixed_audio = audio_streams[0]

    (
        ffmpeg
        .output(video_in.video, mixed_audio, out_path, vcodec="libx264", acodec="aac", crf=18, preset="fast")
        .overwrite_output()
        .run(quiet=True)
    )

    log.info("render.done", path=out_path)
    return out_path


def _concat_audio(spec: VideoSpec, output_path: str) -> None:
    """Concatenate per-scene audio files in scene order."""
    scenes = sorted(spec.scenes, key=lambda s: s.order)
    audio_paths = [s.audio_path for s in scenes if s.audio_path]

    if not audio_paths:
        raise ValueError("No audio paths found in spec. Run voiceover stage first.")

    # Build ffmpeg concat demuxer list
    list_path = output_path + ".txt"
    with open(list_path, "w") as f:
        for p in audio_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")

    (
        ffmpeg
        .input(list_path, format="concat", safe=0)
        .output(output_path, acodec="libmp3lame")
        .overwrite_output()
        .run(quiet=True)
    )
    os.remove(list_path)


def _total_duration(spec: VideoSpec) -> float:
    return sum(s.duration_sec or 0.0 for s in spec.scenes)
