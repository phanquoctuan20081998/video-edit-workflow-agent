"""Pexels stock video provider."""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from app.providers.base import StockClip, StockProvider

_PEXELS_API = "https://api.pexels.com/videos"


class PexelsProvider(StockProvider):
    def __init__(self, api_key: str):
        self._api_key = api_key

    async def search(self, query: str, *, duration_sec: float = 10.0, limit: int = 5) -> list[StockClip]:
        headers = {"Authorization": self._api_key}
        params = {"query": query, "per_page": limit, "orientation": "landscape"}
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{_PEXELS_API}/search", headers=headers, params=params)
            resp.raise_for_status()
        data = resp.json()
        clips = []
        for v in data.get("videos", []):
            best = max(v["video_files"], key=lambda f: f.get("width", 0))
            clips.append(StockClip(
                url=best["link"],
                local_path=None,
                duration_sec=float(v.get("duration", 0)),
                attribution=f"Pexels video by {v['user']['name']}",
            ))
        return clips

    async def download(self, clip: StockClip, dest_dir: str) -> str:
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        filename = clip.url.split("?")[0].rsplit("/", 1)[-1]
        dest = os.path.join(dest_dir, filename)
        async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
            async with client.stream("GET", clip.url) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=8192):
                        f.write(chunk)
        clip.local_path = dest
        return dest
