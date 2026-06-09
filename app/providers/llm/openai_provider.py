"""OpenAI provider — text + vision completion."""

from __future__ import annotations

import base64
from pathlib import Path

from openai import AsyncOpenAI

from app.providers.base import LLMMessage, LLMProvider, LLMResponse


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gpt-4o"):
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        formatted = []
        if system:
            formatted.append({"role": "system", "content": system})
        formatted += [{"role": m.role, "content": m.content} for m in messages]

        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=formatted,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        choice = resp.choices[0]
        # try to capture raw response if possible
        raw = None
        try:
            raw = resp
        except Exception:
            raw = None
        return LLMResponse(
            content=choice.message.content or "",
            model=resp.model,
            input_tokens=resp.usage.prompt_tokens,
            output_tokens=resp.usage.completion_tokens,
            raw=raw,
        )

    async def vision_complete(
        self,
        messages: list[LLMMessage],
        image_paths: list[str],
        *,
        max_tokens: int = 1024,
        **kwargs,
    ) -> LLMResponse:
        content: list = []
        for path in image_paths:
            data = base64.standard_b64encode(Path(path).read_bytes()).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{data}"},
            })
        for m in messages:
            if m.role == "user":
                content.append({"type": "text", "text": m.content})

        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": content}],
            max_tokens=max_tokens,
        )
        choice = resp.choices[0]
        raw = None
        try:
            raw = resp
        except Exception:
            raw = None
        return LLMResponse(
            content=choice.message.content or "",
            model=resp.model,
            input_tokens=resp.usage.prompt_tokens,
            output_tokens=resp.usage.completion_tokens,
            raw=raw,
        )
