"""Provider factory — all pipeline code calls these, never imports SDKs directly."""

from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.providers.base import LLMProvider, StockProvider, TTSProvider


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider:
    cfg = get_settings()
    if cfg.llm_provider == "anthropic":
        from app.providers.llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key=cfg.anthropic_api_key, model=cfg.llm_model)
    if cfg.llm_provider == "openai":
        from app.providers.llm.openai_provider import OpenAIProvider
        return OpenAIProvider(api_key=cfg.openai_api_key, model=cfg.llm_model)
    raise ValueError(f"Unknown LLM provider: {cfg.llm_provider!r}")


@lru_cache(maxsize=1)
def get_vision_provider() -> LLMProvider:
    cfg = get_settings()
    if cfg.vision_provider == "anthropic":
        from app.providers.llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key=cfg.anthropic_api_key, model=cfg.vision_model)
    if cfg.vision_provider == "openai":
        from app.providers.llm.openai_provider import OpenAIProvider
        return OpenAIProvider(api_key=cfg.openai_api_key, model=cfg.vision_model)
    raise ValueError(f"Unknown vision provider: {cfg.vision_provider!r}")


@lru_cache(maxsize=1)
def get_tts_provider() -> TTSProvider:
    cfg = get_settings()
    if cfg.tts_provider == "edge":
        from app.providers.tts.edge_tts_provider import EdgeTTSProvider
        return EdgeTTSProvider(default_voice=cfg.tts_voice)
    if cfg.tts_provider == "azure":
        from app.providers.tts.azure_tts_provider import AzureTTSProvider
        return AzureTTSProvider(
            api_key=cfg.azure_speech_key,
            region=cfg.azure_speech_region,
            default_voice=cfg.tts_voice,
        )
    raise ValueError(f"Unknown TTS provider: {cfg.tts_provider!r}")


@lru_cache(maxsize=1)
def get_stock_provider() -> StockProvider:
    cfg = get_settings()
    from app.providers.stock.pexels_provider import PexelsProvider
    return PexelsProvider(api_key=cfg.pexels_api_key)
