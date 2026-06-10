"""Sample representative frames from a rendered clip for visual QA."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def sample_frames(clip_path: str, n: int = 4, output_dir: str | None = None) -> list[str]:
    """Extract n evenly-spaced frames from clip. Returns list of PNG paths."""
    out_dir = output_dir or str(Path(clip_path).parent / "frames")
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    duration = _get_duration(clip_path)
    if duration <= 0:
        return []

    frame_paths = []
    for i in range(n):
        t = duration * (i + 0.5) / n
        out = os.path.join(out_dir, f"frame_{i:02d}.png")
        subprocess.run(
            [
                "ffmpeg", "-ss", str(t), "-i", clip_path,
                "-frames:v", "1", "-q:v", "2", out, "-y",
            ],
            check=True,
            capture_output=True,
        )
        frame_paths.append(out)

    return frame_paths


def _get_duration(clip_path: str) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            clip_path,
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def motion_score(frame_paths: list[str]) -> float:
    """Mean absolute pixel difference between consecutive sampled frames (0-255).

    A near-zero score means the rendered clip is effectively a static slide —
    the #1 "AI slop" failure mode. Caught here it costs nothing; caught by the
    vision QA model it costs a full vision call plus vaguer repair feedback.

    Returns -1.0 if scoring is unavailable (missing deps / unreadable frames),
    in which case callers should fall through to vision QA.
    """
    if len(frame_paths) < 2:
        return -1.0
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return -1.0

    try:
        arrays = []
        for p in frame_paths:
            img = Image.open(p).convert("L").resize((160, 90))
            arrays.append(np.asarray(img, dtype=np.float32))
        diffs = [
            float(np.abs(arrays[i + 1] - arrays[i]).mean())
            for i in range(len(arrays) - 1)
        ]
        return sum(diffs) / len(diffs)
    except Exception:
        return -1.0
