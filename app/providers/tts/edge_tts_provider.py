"""Edge TTS provider — free TTS with word-level timestamps."""

from __future__ import annotations

from pathlib import Path

import edge_tts
import ffmpeg

from app.providers.base import TTSProvider, TTSResult


class EdgeTTSProvider(TTSProvider):
    def __init__(self, default_voice: str = "vi-VN-HoaiMyNeural"):
        self._default_voice = default_voice

    async def synthesize(
        self,
        text: str,
        output_path: str,
        *,
        voice: str | None = None,
    ) -> TTSResult:
        voice = voice or self._default_voice
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # Edge TTS gives word boundaries via SSML events
        communicate = edge_tts.Communicate(text, voice)
        subtitles = edge_tts.SubMaker()

        with open(output_path, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    _feed_word_boundary(subtitles, chunk)

        # Parse word timestamps from SubMaker
        word_timestamps = _parse_word_timestamps(subtitles)
        duration_sec = word_timestamps[-1]["end"] if word_timestamps else _probe_audio_duration(output_path)

        return TTSResult(
            audio_path=output_path,
            duration_sec=duration_sec,
            word_timestamps=word_timestamps,
        )


def _parse_word_timestamps(subtitles: "edge_tts.SubMaker") -> list[dict]:
    """Convert Edge TTS word boundary events to [{"word", "start", "end"}]."""
    if hasattr(subtitles, "cues"):
        return [
            {
                "word": str(cue.content),
                "start": round(cue.start.total_seconds(), 3),
                "end": round(cue.end.total_seconds(), 3),
            }
            for cue in subtitles.cues
        ]

    results = []
    subs_list = getattr(subtitles, "subs_list", [])
    words_list = getattr(subtitles, "words_list", [])
    for (offset, duration), word in zip(subs_list, words_list):
        start = offset / 1e7   # 100-ns units → seconds
        end = (offset + duration) / 1e7
        results.append({"word": str(word), "start": round(start, 3), "end": round(end, 3)})
    return results


def _feed_word_boundary(subtitles: "edge_tts.SubMaker", chunk: dict) -> None:
    if hasattr(subtitles, "feed"):
        subtitles.feed(chunk)
        return

    subtitles.create_sub(
        (chunk["offset"], chunk["duration"]),
        chunk["text"],
    )


def _probe_audio_duration(audio_path: str) -> float:
    try:
        probe = ffmpeg.probe(audio_path)
    except ffmpeg.Error:
        return 0.0

    duration = probe.get("format", {}).get("duration")
    if duration is None:
        return 0.0
    return round(float(duration), 3)
