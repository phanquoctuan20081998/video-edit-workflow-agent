"""Stage 5 — Composite pipeline (deterministic, no LLM).

Assembles clips according to duration_sec from voiceover stage.
For beat-aware scenes, applies per-beat time stretching to sync animation with narration.
Raises AssertionError if any scene is missing duration_sec — voiceover MUST run first.
Raises RuntimeError if a scene is missing clip_path — animation stage MUST run first.
"""

from __future__ import annotations

import os
from pathlib import Path

import structlog
from moviepy import VideoFileClip, concatenate_videoclips

from app.config import get_settings
from app.models.video_spec import Scene, VideoSpec
from app.pipeline.beat_timing import resolve_beat_timing

log = structlog.get_logger()


async def run_composite(spec: VideoSpec, artifact_dir: str | None = None) -> str:
    """Assemble all scene clips into a single silent video. Returns output path."""
    cfg = get_settings()
    base_dir = artifact_dir or cfg.artifact_dir

    # Invariant: voiceover must have run for all scenes
    missing = [s.id for s in spec.scenes if s.duration_sec is None or s.duration_sec <= 0]
    assert not missing, f"Scenes missing duration_sec (voiceover not run): {missing}"

    # Resolve beat timing now that we have word timestamps
    resolve_beat_timing(spec)

    clips = []
    for scene in sorted(spec.scenes, key=lambda s: s.order):
        clip = _build_scene_clip(scene)
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


def _build_scene_clip(scene: Scene) -> "VideoFileClip":
    """Build a clip for a single scene, applying beat-aware time stretching if available."""
    target_duration = scene.duration_sec

    if scene.clip_path and Path(scene.clip_path).exists():
        clip = VideoFileClip(scene.clip_path)

        if scene.beats_timed and len(scene.beats) > 1:
            # Beat-aware: stretch/compress per-beat segments
            clip = _apply_beat_timing(clip, scene)
        else:
            # Simple: trim or freeze-extend to match voice duration
            clip = _simple_duration_match(clip, target_duration)
    else:
        log.error("composite.missing_clip", scene_id=scene.id, clip_path=scene.clip_path)
        raise RuntimeError(
            f"Scene {scene.id} has no rendered clip — animation stage must complete first. "
            f"clip_path={scene.clip_path!r}"
        )

    return clip


def _simple_duration_match(clip, target: float):
    """Trim or freeze-extend clip to match target duration."""
    if clip.duration > target:
        clip = clip.subclipped(0, target)
    elif clip.duration < target:
        still = clip.to_ImageClip(t=clip.duration - 0.05).with_duration(target - clip.duration)
        clip = concatenate_videoclips([clip, still])
    return clip


def _apply_beat_timing(clip, scene: Scene):
    """Apply per-beat time stretching to sync Manim animation with narration beats.

    Uses scene.beat_render_durations (parsed from Manim code) when available to
    cut the source clip at real animation boundaries. Falls back to equal division.

    Each segment is then speed-adjusted to match the narration beat duration from
    the beat timing resolver.
    """
    n_beats = len(scene.beats)
    src_duration = clip.duration
    target_duration = scene.duration_sec

    # Simple case: if total durations are close, just stretch uniformly
    ratio = target_duration / src_duration if src_duration > 0 else 1.0
    if 0.85 <= ratio <= 1.15:
        return clip.with_effects([_speed_effect(1.0 / ratio)])

    # Determine source segment boundaries
    if scene.beat_render_durations and len(scene.beat_render_durations) == n_beats:
        # Use actual Manim render durations (normalized to real clip length)
        raw = scene.beat_render_durations
        scale = src_duration / sum(raw)
        render_durs = [d * scale for d in raw]
    else:
        # Fallback: equal division
        render_durs = [src_duration / n_beats] * n_beats

    # Cut source clip into segments at render boundaries
    segments = []
    pos = 0.0
    for dur in render_durs:
        end = min(pos + dur, src_duration)
        seg_start = pos
        seg_end = max(end, pos + 0.04)  # minimum 1 frame at 24fps
        segments.append(clip.subclipped(seg_start, min(seg_end, src_duration)))
        pos = end

    # Speed-adjust each segment to match narration beat duration
    beat_clips = []
    beats_sorted = sorted(scene.beats, key=lambda b: b.order)

    for i, (segment, beat) in enumerate(zip(segments, beats_sorted)):
        beat_target = beat.duration_sec or 0.0
        if beat_target <= 0:
            continue

        seg_actual = segment.duration

        if seg_actual > 0:
            speed_ratio = seg_actual / beat_target
            if 0.5 <= speed_ratio <= 2.0:
                segment = segment.with_effects([_speed_effect(speed_ratio)])
            else:
                segment = _simple_duration_match(segment, beat_target)
        else:
            # Zero-length source segment — freeze last valid frame instead of black
            freeze_t = max((i * (src_duration / n_beats)) - 0.05, 0)
            segment = clip.to_ImageClip(t=min(freeze_t, clip.duration - 0.05)).with_duration(beat_target)

        beat_clips.append(segment)

    if beat_clips:
        return concatenate_videoclips(beat_clips)
    return _simple_duration_match(clip, target_duration)


def _speed_effect(factor: float):
    """Create a speed change effect. factor > 1 = faster, < 1 = slower."""
    from moviepy.video.fx import MultiplySpeed
    return MultiplySpeed(factor=factor)
