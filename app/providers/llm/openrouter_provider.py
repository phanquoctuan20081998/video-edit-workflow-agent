"""OpenRouter provider — lightweight HTTP wrapper compatible with OpenRouter's chat API."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
import httpx
import structlog

from app.providers.base import LLMMessage, LLMProvider, LLMResponse

log = structlog.get_logger()

_RETRYABLE_CODES = {429, 500, 502, 503, 504}
_RETRY_DELAYS     = [10, 40, 90]    # 5xx backoff: fast enough for transient errors
_RETRY_DELAYS_429 = [65, 130, 260]  # 429 backoff: free tier resets ~60s
_TIMEOUT = 300.0                    # 5 min — enough for 8 000-token completions


class OpenRouterProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self._api_key = api_key
        self._model = model
        self._endpoint = "https://openrouter.ai/api/v1/chat/completions"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    async def _post_with_retry(self, payload: dict) -> dict:
        last_exc: Exception | None = None
        last_status: int = 0
        max_attempts = len(_RETRY_DELAYS) + 1
        for attempt in range(max_attempts):
            if attempt > 0:
                delays = _RETRY_DELAYS_429 if last_status == 429 else _RETRY_DELAYS
                delay = delays[attempt - 1]
                log.warning("openrouter.retry", attempt=attempt, wait_sec=delay, prev_status=last_status)
                await asyncio.sleep(delay)
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    resp = await client.post(self._endpoint, json=payload, headers=self._headers())
                    if resp.status_code in _RETRYABLE_CODES:
                        log.warning("openrouter.http_error", status=resp.status_code, attempt=attempt)
                        last_status = resp.status_code
                        last_exc = httpx.HTTPStatusError(
                            f"HTTP {resp.status_code}", request=resp.request, response=resp
                        )
                        continue
                    resp.raise_for_status()
                    data: dict = resp.json()
                    # OpenRouter sometimes returns HTTP 200 with error body
                    if "error" in data:
                        err = data["error"]
                        code = err.get("code", 0) if isinstance(err, dict) else 0
                        msg  = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                        log.warning("openrouter.body_error", code=code, message=msg, attempt=attempt)
                        if code in _RETRYABLE_CODES or code == 0:
                            last_exc = RuntimeError(f"Provider error {code}: {msg}")
                            continue
                        raise RuntimeError(f"Provider error {code}: {msg}")
                    return data
            except httpx.TimeoutException as exc:
                log.warning("openrouter.timeout", attempt=attempt)
                last_exc = exc
        raise RuntimeError(f"OpenRouter failed after {len(_RETRY_DELAYS)+1} attempts: {last_exc}")

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

        payload: dict = {
            "model": self._model,
            "messages": formatted,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        for k, v in kwargs.items():
            if k not in payload or k in ("reasoning",):
                payload[k] = v

        data = await self._post_with_retry(payload)
        return _parse_response(data, self._model)

    async def vision_complete(
        self,
        messages: list[LLMMessage],
        image_paths: list[str],
        *,
        max_tokens: int = 1024,
        **kwargs,
    ) -> LLMResponse:
        img_content: list = []
        for path in image_paths:
            b64 = base64.standard_b64encode(Path(path).read_bytes()).decode()
            img_content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
        for m in messages:
            if m.role == "user":
                img_content.append({"type": "text", "text": m.content})

        payload: dict = {
            "model": self._model,
            "messages": [{"role": "user", "content": img_content}],
            "max_tokens": max_tokens,
        }
        for k, v in kwargs.items():
            if k not in payload or k in ("reasoning",):
                payload[k] = v

        data = await self._post_with_retry(payload)
        return _parse_response(data, self._model)


def _parse_response(data: dict, default_model: str) -> LLMResponse:
    try:
        choice = data.get("choices", [])[0]
        message = choice.get("message") if isinstance(choice, dict) else choice
        content = ""
        if isinstance(message, dict):
            content_obj = message.get("content") or ""
            if isinstance(content_obj, str):
                content = content_obj
            elif isinstance(content_obj, list):
                content = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content_obj)
            elif isinstance(content_obj, dict) and "text" in content_obj:
                content = content_obj["text"]
            else:
                content = str(content_obj)
        else:
            content = str(message)

        model = data.get("model", default_model)
        usage = data.get("usage", {}) or {}
        input_tokens  = usage.get("prompt_tokens",     usage.get("input_tokens",  0)) or 0
        output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
    except Exception:
        content = str(data)
        model = default_model
        input_tokens = output_tokens = 0

    return LLMResponse(
        content=content,
        model=model,
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        raw=data,
    )
