"""Azure Speech TTS provider — paid, high quality, word-level timestamps."""

from __future__ import annotations

from app.providers.base import TTSProvider, TTSResult


class AzureTTSProvider(TTSProvider):
    """Stub — implement when Azure Speech key is available."""

    def __init__(self, api_key: str, region: str, default_voice: str = "vi-VN-HoaiMyNeural"):
        self._api_key = api_key
        self._region = region
        self._default_voice = default_voice

    async def synthesize(
        self,
        text: str,
        output_path: str,
        *,
        voice: str | None = None,
    ) -> TTSResult:
        raise NotImplementedError("AzureTTSProvider not yet implemented. Set TTS_PROVIDER=edge to use Edge TTS.")
