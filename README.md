# Video Edit Workflow Agent

Automated pipeline for generating math/physics explainer videos with Manim animation, TTS voiceover, and deterministic composite/render. Built for topics like algorithms, papers, physics, and geometry.

---

## Architecture

### Pipeline Overview

```
[Stage 1] Market Search ──► [HITL: approve topic]
                                     │
[Stage 2] Script Agent  ──► [HITL: approve script]
                                     │
[Stage 3] Manim Codegen ──► self-repair loop ──► visual QA
                                     │
[Stage 4] Voiceover (TTS) ◄── runs BEFORE edit
                                     │
[Stage 5] Composite (MoviePy)  ◄── driven by duration_sec
                                     │
[Stage 6] Render (ffmpeg) ──► final.mp4
```

**Rule:** Only stages 1, 2, and the Manim self-repair loop are agentic. Stages 4–6 are fully deterministic — no LLM touches ffmpeg or MoviePy directly.

---

### Key Architectural Decisions

| # | Decision | Why |
|---|----------|-----|
| D1 | Voiceover runs **before** composite | `duration_sec` from TTS drives the timeline. Wrong order = audio/video desync |
| D2 | `VideoSpec` JSON is the backbone | Every stage reads/writes its own fields. No prose scripts that downstream stages must re-parse |
| D3 | Manim runs in **Docker sandbox** (no network) | LLM-generated code must be isolated |
| D4 | Edit/composite/render are deterministic | LLM decides *what*; code executes *how* |
| D5 | Cache by `manim_code_hash` | Re-render only changed scenes. Manim render is expensive |
| D6 | All LLM/TTS/stock behind provider abstraction | Swap providers via config, no pipeline changes |
| D7 | Artifacts referenced by **path**, not embedded in DB | State (SQLite/Postgres) stays small; binaries stay on disk/S3 |

---

### VideoSpec — Intermediate Representation

All stages share one JSON artifact. Each stage owns specific fields:

```
Stage 2 (Script)    → narration, visual_type, visual_spec
Stage 3 (Manim)     → manim_code, manim_code_hash, clip_path, clip_qa_passed
Stage 4 (Voiceover) → audio_path, duration_sec, word_timestamps   ← timeline source of truth
Stage 5 (Composite) → reads duration_sec, assembles clips
Stage 6 (Render)    → final_video_path
```

A stage may **never** write fields owned by another stage.

---

### Manim Self-Repair Loop

```python
for attempt in range(max_repairs + 1):
    result = sandbox_exec(code)          # Docker, no network, 2 CPU, 2 GB, 90s timeout
    if result.error:
        code = llm_repair(code, traceback=result.traceback)   # path 1: runtime error
        continue
    frames = sample_frames(result.clip, n=4)
    qa = vision_qa(frames, intent=scene.visual_spec)
    if qa.passed:
        return success
    code = llm_repair(code, feedback=qa.issues)               # path 2: visual fail
# after cap → flag for human, never silently emit bad clip
```

Two distinct repair paths — runtime errors (traceback) and visual failures (QA feedback) are handled separately.

---

### Directory Structure

```
app/
  agents/           market_search.py, script.py, manim_codegen.py, visual_qa.py
  pipeline/         voiceover.py, composite.py, render.py, subtitle.py
  sandbox/          runner.py (docker + local fallback), frame_sampler.py
  providers/        base.py, factory.py
    llm/            anthropic_provider.py, openai_provider.py
    tts/            edge_tts_provider.py, azure_tts_provider.py
    stock/          pexels_provider.py
  models/           video_spec.py (Pydantic), project.py (ORM), db.py
  templates/        base.py, vectors.py, functions.py, geometry.py,
                    charts.py, signal.py, matrix.py
  orchestration/    graph.py (LangGraph), tasks.py (Celery), celery_app.py
  config.py

api/
  main.py           FastAPI app
  routes/           projects.py, pipeline.py, hitl.py

webui/
  app.py            Streamlit entry point
  pages/            topic_review.py, script_review.py, scene_review.py

docker/
  manim-sandbox/    Dockerfile, entrypoint.sh

alembic/            DB migrations
resource/           fonts/, songs/
```

---

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11, managed by `uv` |
| Orchestration | LangGraph (agentic pipeline + HITL interrupts) |
| Job queue | Celery + Redis (heavy media jobs) |
| Animation | Manim Community Edition 0.18, Docker sandbox |
| Media | MoviePy 2.x, Pillow, ffmpeg |
| TTS | Edge TTS (free, default) / Azure Speech (paid) |
| LLM | Anthropic Claude / OpenAI (swappable via config) |
| Vision QA | Same LLM provider, vision-capable model |
| DB | SQLite (default) / PostgreSQL (production) |
| API | FastAPI + uvicorn |
| HITL UI | Streamlit |
| Stock (optional) | Pexels API |

---

## Setup

### Prerequisites

- Python 3.11+
- [`uv`](https://github.com/astral-sh/uv) — `pip install uv`
- `ffmpeg` in PATH
- API key for Anthropic or OpenAI

Optional:
- Docker Desktop (for Manim sandbox isolation)
- Redis (for Celery job queue)

### Install

```bash
# Clone
git clone <repo>
cd video-edit-workflow-agent

# Add MPT submodule (stages 4-6 media layer)
git submodule add https://github.com/harry0703/MoneyPrinterTurbo vendor/mpt

# Install dependencies
uv sync

# Copy and fill env
cp .env.example .env
```

Edit `.env` — minimum required:

```env
ANTHROPIC_API_KEY=sk-ant-...    # or OPENAI_API_KEY
LLM_PROVIDER=anthropic
SANDBOX_MODE=local               # use "docker" if Docker is available
```

### Database

```bash
make migrate
# or: alembic upgrade head
```

Creates `video_agent.db` (SQLite) in the project root.

---

## Running

### Option A — Streamlit UI (recommended for first run)

```bash
make webui
# opens http://localhost:8501
```

Walk through 3 pages:
1. **Topic Review** — run market search, pick or type a topic
2. **Script Review** — generate + edit VideoSpec scene-by-scene
3. **Scene QA** — render Manim scenes, review clips, approve/reject

### Option B — FastAPI + worker

Terminal 1 — API server:
```bash
make dev
# http://localhost:8000/docs
```

Terminal 2 — Celery worker (needed for render tasks):
```bash
make worker
# requires Redis: docker run -d -p 6379:6379 redis:7-alpine
```

### Option C — Full Docker Compose

```bash
docker-compose up
```

Services: `api` (8000), `webui` (8501), `worker`, `redis`.

### Option D — CLI end-to-end smoke test

```bash
make smoke
# or: python -m app.orchestration.graph "Fast Fourier Transform"
```

Runs full pipeline in terminal. Pauses at HITL checkpoints (prints state, waits for `approve_topic` / `approve_script` API calls to continue).

---

## Manim Sandbox

### Docker mode (production, safe)

```bash
make sandbox-build
# sets SANDBOX_MODE=docker in .env
```

Container: no network, 2 CPU, 2 GB RAM, 90s timeout, read-only filesystem.

### Local mode (dev, no Docker needed)

```env
SANDBOX_MODE=local
```

Requires `manim` installed locally:
```bash
pip install manim==0.18.1
```

**Warning:** Local mode runs LLM-generated code without isolation. Dev only.

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite+aiosqlite:///./video_agent.db` | SQLite or `postgresql+asyncpg://...` |
| `LLM_PROVIDER` | `anthropic` | `anthropic` \| `openai` |
| `LLM_MODEL` | `claude-sonnet-4-6` | Model ID |
| `ANTHROPIC_API_KEY` | — | Required if `LLM_PROVIDER=anthropic` |
| `OPENAI_API_KEY` | — | Required if `LLM_PROVIDER=openai` |
| `VISION_PROVIDER` | `anthropic` | Provider for visual QA |
| `VISION_MODEL` | `claude-sonnet-4-6` | Must support image input |
| `TTS_PROVIDER` | `edge` | `edge` (free) \| `azure` (paid) |
| `TTS_VOICE` | `vi-VN-HoaiMyNeural` | BCP-47 voice name |
| `SANDBOX_MODE` | `local` | `docker` \| `local` |
| `SANDBOX_TIMEOUT` | `90` | Seconds before hard kill |
| `ARTIFACT_DIR` | `./artifacts` | Output directory for clips/audio |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Redis URL |
| `PEXELS_API_KEY` | — | Optional, for stock B-roll |
| `REDDIT_CLIENT_ID` | — | Optional, for market search |

---

## API Endpoints

```
GET    /projects/                       list all projects
POST   /projects/?topic=FFT             create project
GET    /projects/{id}                   get project + spec
DELETE /projects/{id}                   delete

POST   /pipeline/{id}/start             trigger full render (Celery)
POST   /pipeline/{id}/render_scene/{s}  render single scene
GET    /pipeline/tasks/{task_id}        poll Celery task status

POST   /hitl/{id}/approve_topic         approve topic (HITL checkpoint 1)
POST   /hitl/{id}/approve_script        approve VideoSpec (HITL checkpoint 2)
POST   /hitl/{id}/approve_scene/{s}     manually approve a scene clip
```

Interactive docs: `http://localhost:8000/docs`

---

## Manim Template Library

LLM fills params dict → template emits complete Manim CE source. Prefer over free-form codegen.

```python
from app.templates import render_template

code = render_template("vector_sum", {
    "vectors": [[1, 2], [3, -1], [-2, 1]],
    "labels": ["a", "b", "c"],
})
```

| Template name | Description |
|---------------|-------------|
| `vector_sum` | Tip-to-tail vector addition with resultant |
| `vector_field` | 2D arrow vector field |
| `function_graph` | One or more functions on labeled axes |
| `derivative_tangent` | Function with animated tangent line |
| `polygon_transform` | Polygon with rotate/scale/reflect steps |
| `circle_theorem` | Circle with inscribed angles and arcs |
| `bar_chart` | Animated bar chart |
| `number_line` | Number line with highlighted points/intervals |
| `waveform` | Single or sum-of-sinusoids waveform |
| `frequency_spectrum` | FFT bar spectrum with labeled frequencies |
| `matrix_mult` | Step-by-step matrix multiplication |
| `eigenvalue` | Eigenvector arrows on a number plane |

---

## Known Limitations

1. **Manim quality** — code runs but objects may overlap or formulas overflow. Visual QA catches most issues; templates reduce occurrence.
2. **Audio/animation sync** — v1 does not align animation beats to narration timestamps. Scene duration matches voice duration, but internal animation timing is independent.
3. **Stock B-roll** — Pexels integration exists but most math/physics topics have no useful stock. Treat as optional.
4. **Cost** — Stage 3 (Manim repair loop) and Stage 6 (render) are expensive. Cache hits (`manim_code_hash`) prevent redundant renders.
5. **MPT submodule** — must be added manually: `git submodule add https://github.com/harry0703/MoneyPrinterTurbo vendor/mpt`
