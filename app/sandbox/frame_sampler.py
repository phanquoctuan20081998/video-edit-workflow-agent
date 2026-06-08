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
