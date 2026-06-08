"""Anthropic provider — text + vision completion."""

from __future__ import annotations

import base64
from pathlib import Path

import anthropic

from app.providers.base import LLMMessage, LLMProvider, LLMResponse


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
    ) -> LLMResponse:
        kwargs: dict = dict(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": m.role, "content": m.content} for m in messages],
        )
        if system:
            kwargs["system"] = system
        resp = await self._client.messages.create(**kwargs)
        return LLMResponse(
            content=resp.content[0].text,
            model=resp.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )

    async def vision_complete(
        self,
        messages: list[LLMMessage],
        image_paths: list[str],
        *,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        image_blocks = []
        for path in image_paths:
            data = base64.standard_b64encode(Path(path).read_bytes()).decode()
            image_blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": data},
            })

        content: list = image_blocks
        for m in messages:
            if m.role == "user":
                content.append({"type": "text", "text": m.content})

        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": content}],
        )
        return LLMResponse(
            content=resp.content[0].text,
            model=resp.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )
