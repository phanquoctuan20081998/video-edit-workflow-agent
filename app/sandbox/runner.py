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
                env=_local_manim_env(),
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
                traceback=_enrich_latex_error(proc.stderr),
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


def _local_manim_env() -> dict[str, str]:
    env = os.environ.copy()
    texlive_root = Path("/opt/homebrew/opt/texlive/share")
    texlive_dist = texlive_root / "texmf-dist"
    if texlive_dist.exists():
        env.setdefault("TEXMFROOT", str(texlive_root))
        env.setdefault("TEXMFDIST", str(texlive_dist))
        env.setdefault("TEXMFMAIN", str(texlive_dist))
        env.setdefault("TEXMFCNF", str(texlive_dist / "web2c"))
    return env


def _enrich_latex_error(stderr: str) -> str:
    log_path = _extract_latex_log_path(stderr)
    if not log_path or not log_path.exists():
        return stderr

    lines = [line.rstrip() for line in log_path.read_text(errors="replace").splitlines()]
    useful_lines = []
    for index, line in enumerate(lines):
        if line.startswith("! "):
            useful_lines.extend(lines[index:index + 12])

    if not useful_lines:
        useful_lines = lines[-40:]

    return (
        stderr.rstrip()
        + "\n\n=== LaTeX log excerpt ===\n"
        + "\n".join(useful_lines[-80:])
    )


def _extract_latex_log_path(stderr: str) -> Path | None:
    match = re.search(r"the log file:\s*\n?\s*([^\n]+\.log)", stderr)
    if not match:
        return None
    return Path(match.group(1).strip())


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
