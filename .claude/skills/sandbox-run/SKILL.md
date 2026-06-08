---
name: sandbox-run
description: >
  Execute a Manim scene in the Docker sandbox. Handles image build, resource limits,
  timeout enforcement, output retrieval, and frame sampling for QA.
  Invoke manually with /sandbox-run. Do NOT auto-trigger.
disable-model-invocation: true
type: task
---

# sandbox-run — Docker Sandbox Execution for Manim

Manual invocation only: `/sandbox-run`

## Docker image

Image: `manim-sandbox` (local build from `docker/manim-sandbox/Dockerfile`)

Build (one-time, or after deps change):
```bash
docker build -t manim-sandbox ./docker/manim-sandbox/
```

Dockerfile must include:
- `manim` CE (pinned version matching `requirements.txt`)
- NO network tools (no `curl`, `wget`, `pip install` at runtime)
- Non-root user `appuser` owning `/workspace`

## Run command

```bash
docker run --rm \
  --network none \
  --cpus 2 \
  --memory 2g \
  --memory-swap 2g \
  --pids-limit 64 \
  --read-only \
  --tmpfs /tmp:size=256m \
  --tmpfs /workspace:size=512m \
  -v "$(pwd)/sandbox_input:/workspace/input:ro" \
  -v "$(pwd)/sandbox_output:/workspace/output:rw" \
  --timeout 90 \
  manim-sandbox \
  python /workspace/input/scene.py --output_dir /workspace/output
```

Caller writes scene source to `sandbox_input/scene.py` before invoking.
Output mp4 lands in `sandbox_output/`.

## Resource limits (rationale)

| Limit | Value | Reason |
|-------|-------|--------|
| `--network none` | enforced | LLM-generated code, no exfil |
| `--cpus 2` | hard | prevent worker starvation |
| `--memory 2g` | hard | Manim + Python overhead ~500MB; 2g headroom |
| `--pids-limit 64` | hard | prevent fork bomb |
| `--read-only` + tmpfs | enforced | container can't persist state |
| timeout 90s | kill | prevents infinite animation loop |

## Frame sampling for QA

After successful render, sample 4 representative frames:

```python
import subprocess, os

def sample_frames(clip_path: str, n: int = 4) -> list[str]:
    """Returns list of paths to extracted PNG frames."""
    duration_cmd = ["ffprobe", "-v", "error", "-show_entries",
                    "format=duration", "-of", "csv=p=0", clip_path]
    duration = float(subprocess.check_output(duration_cmd))
    
    output_dir = os.path.dirname(clip_path)
    frame_paths = []
    for i in range(n):
        t = duration * (i + 0.5) / n
        out = os.path.join(output_dir, f"frame_{i:02d}.png")
        subprocess.run([
            "ffmpeg", "-ss", str(t), "-i", clip_path,
            "-frames:v", "1", "-q:v", "2", out, "-y"
        ], check=True, capture_output=True)
        frame_paths.append(out)
    return frame_paths
```

Pass `frame_paths` to vision QA. See `manim-scene` skill for QA criteria.

## Output contract

Success:
```python
{
  "success": True,
  "clip_path": "sandbox_output/SceneName.mp4",
  "stdout": "...",
  "stderr": "...",   # Manim logs, not errors
  "wall_time_sec": 34.2
}
```

Failure:
```python
{
  "success": False,
  "error_type": "runtime_error" | "timeout" | "oom",
  "traceback": "...",   # pass to llm_repair in manim-scene skill
  "stdout": "...",
  "stderr": "..."
}
```

`error_type` distinction matters for repair strategy:
- `runtime_error` → traceback → code fix
- `timeout` → animation too long, reduce `run_time` or split scene
- `oom` → too many Mobjects or large ImageMobject, simplify scene

## Checklist before invoking

- [ ] `sandbox_input/scene.py` written
- [ ] `sandbox_output/` directory exists and is empty (avoid stale clips)
- [ ] Docker daemon running
- [ ] `manim-sandbox` image built and up to date
- [ ] `scene.manim_code_hash` computed — check cache before exec

## Cache check

```python
existing = db.query(Scene).filter_by(
    manim_code_hash=scene.manim_code_hash
).first()
if existing and os.path.exists(existing.clip_path):
    return {"success": True, "clip_path": existing.clip_path, "cached": True}
```

Skip `sandbox-run` entirely on cache hit. Log cache hit rate per project.
