"""Edge TTS provider — free, word-level timestamps via WhisperX fallback."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import edge_tts

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
                    subtitles.create_sub(
                        (chunk["offset"], chunk["duration"]),
                        chunk["text"],
                    )

        # Parse word timestamps from SubMaker
        word_timestamps = _parse_word_timestamps(subtitles)
        duration_sec = word_timestamps[-1]["end"] if word_timestamps else 0.0

        return TTSResult(
            audio_path=output_path,
            duration_sec=duration_sec,
            word_timestamps=word_timestamps,
        )


def _parse_word_timestamps(subtitles: "edge_tts.SubMaker") -> list[dict]:
    """Convert Edge TTS word boundary events to [{"word", "start", "end"}]."""
    results = []
    for (offset, duration), word in zip(subtitles.subs_list, subtitles.words_list if hasattr(subtitles, 'words_list') else []):
        start = offset / 1e7   # 100-ns units → seconds
        end = (offset + duration) / 1e7
        results.append({"word": str(word), "start": round(start, 3), "end": round(end, 3)})
    return results
