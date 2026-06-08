"""Manim sandbox runner.

SANDBOX_MODE=docker  — Docker container, no network, resource-limited (production)
SANDBOX_MODE=local   — direct Python exec, no isolation (dev only)
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from app.config import get_settings

log = structlog.get_logger()


@dataclass
class SandboxResult:
    success: bool
    clip_path: str | None = None
    error_type: str | None = None   # "runtime_error" | "timeout" | "oom"
    traceback: str | None = None
    stdout: str = ""
    stderr: str = ""
    wall_time_sec: float = 0.0


def sandbox_exec(code: str, output_dir: str | None = None) -> SandboxResult:
    """Execute Manim scene code in the configured sandbox. Blocking."""
    cfg = get_settings()

    if cfg.sandbox_mode == "docker":
        return _exec_docker(code, output_dir, cfg)
    elif cfg.sandbox_mode == "local":
        return _exec_local(code, output_dir, cfg)
    else:
        raise ValueError(f"Unknown SANDBOX_MODE: {cfg.sandbox_mode!r}")


# ── Docker mode ────────────────────────────────────────────────────────────────

def _exec_docker(code: str, output_dir: str | None, cfg) -> SandboxResult:
    with tempfile.TemporaryDirectory() as tmpdir:
        input_dir = os.path.join(tmpdir, "input")
        out_dir = output_dir or os.path.join(tmpdir, "output")
        os.makedirs(input_dir, exist_ok=True)
        os.makedirs(out_dir, exist_ok=True)

        scene_file = os.path.join(input_dir, "scene.py")
        Path(scene_file).write_text(code)

        cmd = [
            "docker", "run", "--rm",
            "--network", "none",
            "--cpus", "2",
            "--memory", "2g",
            "--memory-swap", "2g",
            "--pids-limit", "64",
            "--read-only",
            "--tmpfs", "/tmp:size=256m",
            "--tmpfs", "/workspace:size=512m",
            "-v", f"{input_dir}:/workspace/input:ro",
            "-v", f"{out_dir}:/workspace/output:rw",
            cfg.sandbox_docker_image,
        ]

        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=cfg.sandbox_timeout,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                success=False,
                error_type="timeout",
                traceback=f"Exceeded {cfg.sandbox_timeout}s timeout",
                wall_time_sec=cfg.sandbox_timeout,
            )

        elapsed = time.monotonic() - t0
        clip = _find_output_clip(out_dir)

        if proc.returncode != 0 or clip is None:
            err_type = "oom" if "OOM" in proc.stderr or "killed" in proc.stderr.lower() else "runtime_error"
            return SandboxResult(
                success=False,
                error_type=err_type,
                traceback=proc.stderr,
                stdout=proc.stdout,
                stderr=proc.stderr,
                wall_time_sec=elapsed,
            )

        return SandboxResult(
            success=True,
            clip_path=clip,
            stdout=proc.stdout,
            stderr=proc.stderr,
            wall_time_sec=elapsed,
        )


# ── Local mode (dev only) ──────────────────────────────────────────────────────

def _exec_local(code: str, output_dir: str | None, cfg) -> SandboxResult:
    """Run Manim directly. No isolation — dev only. Requires manim in PATH."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = output_dir or os.path.join(tmpdir, "output")
        os.makedirs(out_dir, exist_ok=True)

        scene_file = os.path.join(tmpdir, "scene.py")
        Path(scene_file).write_text(code)

        scene_class = _extract_scene_class(code)
        if not scene_class:
            return SandboxResult(
                success=False,
                error_type="runtime_error",
                traceback="No Scene subclass found in generated code.",
            )

        cmd = [
            sys.executable, "-m", "manim", "render",
            "--output_dir", out_dir,
            "--media_dir", out_dir,
            "--quality", "l",
            "--format", "mp4",
            "--disable_caching",
            scene_file,
            scene_class,
        ]

        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=cfg.sandbox_timeout,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                success=False,
                error_type="timeout",
                traceback=f"Exceeded {cfg.sandbox_timeout}s timeout",
                wall_time_sec=cfg.sandbox_timeout,
            )

        elapsed = time.monotonic() - t0
        clip = _find_output_clip(out_dir)

        if proc.returncode != 0 or clip is None:
            return SandboxResult(
                success=False,
                error_type="runtime_error",
                traceback=proc.stderr,
                stdout=proc.stdout,
                stderr=proc.stderr,
                wall_time_sec=elapsed,
            )

        return SandboxResult(
            success=True,
            clip_path=clip,
            stdout=proc.stdout,
            stderr=proc.stderr,
            wall_time_sec=elapsed,
        )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _find_output_clip(output_dir: str) -> str | None:
    for root, _, files in os.walk(output_dir):
        for f in files:
            if f.endswith(".mp4"):
                return os.path.join(root, f)
    return None


def _extract_scene_class(code: str) -> str | None:
    m = re.search(r"class\s+(\w+)\s*\(\s*Scene\s*\)", code)
    return m.group(1) if m else None


# ── CLI test entrypoint ────────────────────────────────────────────────────────

if __name__ == "__main__":
    TEST_CODE = '''
from manim import *

class TestScene(Scene):
    def construct(self):
        circle = Circle(color=BLUE)
        self.play(Create(circle))
        self.wait(1)
'''
    result = sandbox_exec(TEST_CODE)
    print(f"success={result.success} clip={result.clip_path} time={result.wall_time_sec:.1f}s")
    if not result.success:
        print(f"error_type={result.error_type}")
        print(result.traceback)
