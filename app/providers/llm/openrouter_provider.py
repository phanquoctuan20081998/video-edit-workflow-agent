"""OpenRouter provider — lightweight HTTP wrapper compatible with OpenRouter's chat API."""

from __future__ import annotations

import base64
from pathlib import Path
import httpx

from app.providers.base import LLMMessage, LLMProvider, LLMResponse


class OpenRouterProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self._api_key = api_key
        self._model = model
        self._endpoint = "https://openrouter.ai/api/v1/chat/completions"

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

        payload = {
            "model": self._model,
            "messages": formatted,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # Pass-through extra top-level params, e.g., reasoning
        if kwargs:
            for k, v in kwargs.items():
                # Do not overwrite core fields unless explicitly provided
                if k not in payload or k in ("reasoning",):
                    payload[k] = v

        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(self._endpoint, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        # Best-effort parsing for OpenRouter/OpenAI-compatible response shapes
        try:
            choice = data.get("choices", [])[0]
            message = choice.get("message") if isinstance(choice, dict) else choice
            content = ""
            if isinstance(message, dict):
                content_obj = message.get("content") or message.get("content", "")
                if isinstance(content_obj, str):
                    content = content_obj
                elif isinstance(content_obj, list):
                    content = "".join([p.get("text", "") if isinstance(p, dict) else str(p) for p in content_obj])
                elif isinstance(content_obj, dict) and "text" in content_obj:
                    content = content_obj["text"]
                else:
                    content = str(content_obj)
            else:
                content = str(message)

            model = data.get("model", self._model)
            usage = data.get("usage", {}) or {}
            input_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
            output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
        except Exception:
            content = str(data)
            model = self._model
            input_tokens = 0
            output_tokens = 0

        return LLMResponse(
            content=content,
            model=model,
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
            raw=data,
        )

    async def vision_complete(
        self,
        messages: list[LLMMessage],
        image_paths: list[str],
        *,
        max_tokens: int = 1024,
        **kwargs,
    ) -> LLMResponse:
        """Handle vision completions with image analysis."""
        content: list = []
        
        # Add images to content
        for path in image_paths:
            data = base64.standard_b64encode(Path(path).read_bytes()).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{data}"},
            })
        
        # Add text messages
        for m in messages:
            if m.role == "user":
                content.append({"type": "text", "text": m.content})

        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
        }
        
        # Pass through extra params
        if kwargs:
            for k, v in kwargs.items():
                if k not in payload or k in ("reasoning",):
                    payload[k] = v

        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(self._endpoint, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        # Parse response same as complete()
        try:
            choice = data.get("choices", [])[0]
            message = choice.get("message") if isinstance(choice, dict) else choice
            content_str = ""
            if isinstance(message, dict):
                content_obj = message.get("content") or ""
                if isinstance(content_obj, str):
                    content_str = content_obj
                elif isinstance(content_obj, list):
                    content_str = "".join([p.get("text", "") if isinstance(p, dict) else str(p) for p in content_obj])
                else:
                    content_str = str(content_obj)
            else:
                content_str = str(message)

            model = data.get("model", self._model)
            usage = data.get("usage", {}) or {}
            input_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
            output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
        except Exception:
            content_str = str(data)
            model = self._model
            input_tokens = 0
            output_tokens = 0

        return LLMResponse(
            content=content_str,
            model=model,
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
            raw=data,
        )
