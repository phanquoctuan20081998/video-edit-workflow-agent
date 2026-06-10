"""Stage 4 — Voiceover pipeline.

Calls TTS provider per scene, fills:
  scene.audio_path, scene.duration_sec, scene.word_timestamps

duration_sec becomes the source of truth for the timeline in Stage 5.
MUST run before composite. Raises if called after timeline is finalized.
"""

from __future__ import annotations

import os
from pathlib import Path

import ffmpeg
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
        if (
            scene.audio_path
            and Path(scene.audio_path).exists()
            and scene.duration_sec
            and scene.duration_sec > 0
        ):
            # Cached, but if word timestamps are missing the beat resolver cannot
            # sync animation to narration — try to recover them via Whisper.
            if scene.has_beats and not scene.word_timestamps:
                recovered = _transcribe_word_timestamps(scene.audio_path, spec.language)
                if recovered:
                    scene.word_timestamps = recovered
                    log.info(
                        "voiceover.recovered_word_timestamps",
                        scene_id=scene.id,
                        words=len(recovered),
                    )
            log.info("voiceover.skip_cached", scene_id=scene.id)
            continue

        if scene.audio_path and Path(scene.audio_path).exists():
            recovered_duration = _probe_audio_duration(scene.audio_path)
            if recovered_duration > 0:
                scene.duration_sec = recovered_duration
                if not scene.word_timestamps:
                    scene.word_timestamps = (
                        _transcribe_word_timestamps(scene.audio_path, spec.language) or []
                    )
                log.info(
                    "voiceover.recovered_cached_duration",
                    scene_id=scene.id,
                    duration=recovered_duration,
                )
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


def _probe_audio_duration(audio_path: str) -> float:
    try:
        probe = ffmpeg.probe(audio_path)
    except ffmpeg.Error:
        return 0.0

    duration = probe.get("format", {}).get("duration")
    if duration is None:
        return 0.0
    return round(float(duration), 3)


def _transcribe_word_timestamps(
    audio_path: str, language: str | None = None
) -> list[WordTimestamp] | None:
    """Recover word-level timestamps from audio using faster-whisper.

    Used when cached audio exists but word_timestamps were lost (e.g. project
    reloaded from disk). Without word timestamps, beat timing falls back to
    equal distribution and the animation drifts from the narration.

    Returns None if faster-whisper is unavailable or transcription fails.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log.warning("voiceover.whisper_unavailable", audio=audio_path)
        return None

    try:
        model = _get_whisper_model()
        segments, _info = model.transcribe(
            audio_path,
            language=(language or None) if language != "auto" else None,
            word_timestamps=True,
            vad_filter=True,
        )
        words: list[WordTimestamp] = []
        for seg in segments:
            for w in seg.words or []:
                token = w.word.strip()
                if token:
                    words.append(
                        WordTimestamp(word=token, start=round(w.start, 3), end=round(w.end, 3))
                    )
        return words or None
    except Exception as exc:  # noqa: BLE001 — best-effort recovery
        log.warning("voiceover.whisper_failed", audio=audio_path, error=str(exc))
        return None


_WHISPER_MODEL = None


def _get_whisper_model():
    """Lazy singleton — loading the model is slow, do it once per process."""
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        from faster_whisper import WhisperModel
        _WHISPER_MODEL = WhisperModel("small", device="cpu", compute_type="int8")
    return _WHISPER_MODEL
