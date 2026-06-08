"""Abstract base classes for all external provider types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class LLMMessage:
    role: str   # "user" | "assistant" | "system"
    content: str | list[dict]   # str for text, list for multimodal


@dataclass
class LLMResponse:
    content: str
    model: str
    input_tokens: int
    output_tokens: int


@dataclass
class TTSResult:
    audio_path: str
    duration_sec: float
    word_timestamps: list[dict]   # [{"word": str, "start": float, "end": float}]


@dataclass
class StockClip:
    url: str
    local_path: str | None
    duration_sec: float
    attribution: str


class LLMProvider(ABC):
    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        system: str | None = None,
    ) -> LLMResponse: ...

    async def vision_complete(
        self,
        messages: list[LLMMessage],
        image_paths: list[str],
        *,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        raise NotImplementedError(f"{type(self).__name__} does not support vision")


class TTSProvider(ABC):
    @abstractmethod
    async def synthesize(
        self,
        text: str,
        output_path: str,
        *,
        voice: str | None = None,
    ) -> TTSResult: ...


class StockProvider(ABC):
    @abstractmethod
    async def search(self, query: str, *, duration_sec: float = 10.0, limit: int = 5) -> list[StockClip]: ...

    @abstractmethod
    async def download(self, clip: StockClip, dest_dir: str) -> str: ...
