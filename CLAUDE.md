# CLAUDE.md — Explainer Video Agent System

Context document for coding agents and developers. Read before writing code.
Goal: an agent system that automatically produces explainer videos about **algorithms, papers, physics/mathematics**, with visuals limited to **math, geometry, and charts** (rendered by Manim).

---

## 1. Overview

6-stage pipeline with human-in-the-loop (HITL) at the first 2 checkpoints:

1. **Market search** — find trending topics, filter by visualizability → user approves.
2. **Script** — from approved topic, research then emit `VideoSpec` (not prose) → user approves.
3. **Manim codegen** — generate Python (Manim) code per scene, run in sandbox, self-repair errors, QA via vision model.
4. **Voiceover** — TTS each scene, obtain duration + word-level timestamps. **Runs BEFORE edit.**
5. **Composite + subtitle** — assemble Manim clips + (optional B-roll stock) on a timeline driven by voiceover duration, render subtitles.
6. **Render** — mux video + audio + subtitles + background music, encode.

### Most important architectural principle
Only **3 parts** are truly agentic: market search, script research, and the Manim self-repair loop. The remaining four (fetch stock, assemble timeline, mux, encode) must be **deterministic**. **Never let an LLM control individual ffmpeg/MoviePy commands** — too expensive and not reproducible.

---

## 2. Core Architecture Decisions (DO NOT VIOLATE)

| # | Decision | Reason |
|---|----------|--------|
| D1 | **Voiceover generated BEFORE edit** | Narration length determines how long each scene is displayed. Without duration, the cut cannot be finalized. Wrong order → audio/video drift. |
| D2 | **`VideoSpec` (structured JSON) is the backbone**, not prose script | Every downstream stage reads/writes its own fields on the same artifact. A prose-only script agent forces downstream stages to guess structure. |
| D3 | **Manim code runs in a Docker sandbox, no network**, with CPU/RAM/timeout limits | We are executing LLM-generated code. Isolation is mandatory. |
| D4 | **Edit/composite/render is deterministic** (MoviePy + ffmpeg) | LLM only *chooses* resources/cut decisions; pure code *executes* the assembly. |
| D5 | **Cache by content-hash** | Changing 1 scene only re-renders that scene. Manim render is expensive. |
| D6 | **Abstracting LLM/TTS/stock behind interface + config** | Switching providers does not require changing the pipeline. |
| D7 | **Artifacts referenced by path, not embedded as binary in state/JSON** | State (Postgres) stays lean; binaries live in object storage / local fs. |

---

## 3. Reuse from MoneyPrinterTurbo (MPT)

MPT (`github.com/harry0703/MoneyPrinterTurbo`) handles **the back half of the pipeline well** but **is not an agent system** and **has nothing for Manim**. Strategy: borrow the media layer, build the agentic front half ourselves, treat Manim clips as "material" input to MPT's compositor.

**Reusable (stages 4–6 + cross-cutting):**
- TTS + subtitle timing: two modes — `edge` (timestamps from Edge TTS, fast, no GPU) as default; `whisper` (faster-whisper, smoother timestamps, requires ~3GB model) as fallback when long sentences or formulas cause sync drift.
- Composite stack: **MoviePy 2.x + Pillow** (render subtitles with Pillow, no ImageMagick needed) + ffmpeg.
- Provider abstraction: multiple LLM providers via config; TTS split between free Edge TTS / paid Azure TTS V2.
- Background music + segment length config (controls scene transition frequency).
- Streamlit WebUI + FastAPI (MVC) — used for the 2 HITL checkpoints.

**NOT reused / must build ourselves:**
- Market/trending agent, research loop, topic approval loop — MPT has none of these.
- **Self-repair loop for codegen — MPT has none. This is real engineering.**
- Replace MPT's "stock material = keyword search" stage with "Manim render". Interface is the same (scene → mp4 clip) but Manim is much more expensive, per-scene custom, and requires a sandbox.

> **Note:** Stock B-roll is mostly useless for math/physics videos. Keep it as a nice-to-have for intros/transitions, NOT as a main component. Value lies in Manim animation.

---

## 4. `VideoSpec` — Intermediate Representation (backbone)

Produced by the script agent. Every downstream stage progressively fills in `null` fields.
Each scene is a "chapter" (30–120s) containing multiple **beats** — the unit of sync between narration and visuals.

```json
{
  "project_id": "uuid",
  "topic": "Fast Fourier Transform",
  "source_refs": ["arxiv:1234.5678", "https://..."],
  "language": "en",
  "aspect_ratio": "16:9",
  "status": "scripted",            // searched|scripted|approved|animated|voiced|composited|rendered
  "scenes": [
    {
      "id": "s01",
      "order": 1,
      "narration": "Imagine a signal. It is actually a sum of rotating vectors. When we add them together, we get a square wave.",
      "visual_type": "manim",      // manim | stock | chart | static_image | title_card
      "visual_spec": "rotating phasors summing into a square wave; highlight frequency components",
      "beats": [                   // intra-scene sync points (Stage 2 sets these)
        {
          "id": "s01_b01",
          "order": 1,
          "trigger_phrase": "Imagine a signal",
          "visual_action": "Create signal waveform on axes",
          "narration_segment": "Imagine a signal.",
          "start_sec": null,       // filled by beat_timing resolver (after stage 4)
          "duration_sec": null
        },
        {
          "id": "s01_b02",
          "order": 2,
          "trigger_phrase": "sum of rotating vectors",
          "visual_action": "Transform signal into rotating phasors",
          "narration_segment": "It is actually a sum of rotating vectors.",
          "start_sec": null,
          "duration_sec": null
        },
        {
          "id": "s01_b03",
          "order": 3,
          "trigger_phrase": "add them together",
          "visual_action": "Animate phasor sum converging to square wave",
          "narration_segment": "When we add them together, we get a square wave.",
          "start_sec": null,
          "duration_sec": null
        }
      ],
      "manim_code": null,          // filled in stage 3 (ONE continuous scene for all beats)
      "manim_code_hash": null,     // cache key
      "clip_path": null,           // filled in stage 3 (rendered scene output)
      "clip_qa_passed": null,      // bool, from visual QA
      "audio_path": null,          // filled in stage 4
      "duration_sec": null,        // filled in stage 4 — DRIVES timeline
      "word_timestamps": null,     // filled in stage 4 (for subtitle sync + beat timing)
      "stock_query": null,         // if visual_type=stock
      "stock_clip_path": null
    }
  ],
  "bgm": { "path": null, "volume": 0.15 },
  "subtitle_style": { "font": "...", "size": 42, "color": "#FFF", "stroke": true },
  "final_video_path": null
}
```

Rules:
- Each stage may only write its own fields.
- `duration_sec` is set exclusively in stage 4 (voiceover) and is the source of truth for the stage 5 timeline.
- `beats[].start_sec` and `beats[].duration_sec` are computed by the beat_timing resolver (between stages 4 and 5), based on word_timestamps + trigger_phrase matching.
- Manim codegen generates **ONE continuous Scene** for all beats in a scene — objects persist across beats.

---

## 5. Per-Stage Details

### Stage 1 — Market search agent (agentic)
- Sources: arXiv (new/trending papers), Reddit (r/math, r/Physics, r/MachineLearning), Hacker News, YouTube niche search "explained", Google Trends.
- Score on **2 independent axes**: (a) trending score, (b) **Manim visualizability** (math/geometry/charts). Without axis (b), the agent will continually suggest topics that can't be animated.
- Output: ranked topic list + reasoning + estimated difficulty + recommended approach.
- HITL: user selects. Feedback saved to refine future queries.

### Stage 2 — Script agent (agentic, sub-pipeline)
- `research (fetch + parse PDF/sources, RAG) → outline → write script → emit VideoSpec`.
- Output is a `VideoSpec` with `scenes[]` divided into chapters, each with `narration` + `visual_type` + `visual_spec` + `beats[]`.
- Each beat = one visual transition in a continuous animation. `trigger_phrase` = exact substring of narration, used to sync timing after TTS.
- HITL: user reviews/edits (including beats).

### Stage 3 — Manim codegen (agentic, hardest part)
- Input: scenes with `visual_type ∈ {manim, chart}` and their `beats[]`.
- Codegen generates **ONE continuous Scene** for all beats — objects persist across beats (like 3Blue1Brown).
- Code is marked with `# ═══ BEAT {id} ═══` + `self.wait()` at beat boundaries so the compositor can compute timing.
- Loop: generate → sandbox exec → repair → visual QA (see section 6).
- Output: `clip_path` + `clip_qa_passed` per scene.
- **Strong recommendation: parametrized Manim template library** (vectors, functions, geometric transforms, charts) so the LLM fills in parameters rather than writing free-form code → trades flexibility for reliability. For this project's narrow scope, this is the most practical lever.
- **Batch-generate-and-pick** for difficult scenes: generate N variants, QA + human picks (reduces the risk of "runs but looks wrong").

### Stage 4 — Voiceover (deterministic, MPT)
- TTS each scene → `audio_path`, `duration_sec`, `word_timestamps`.
- Timestamps: `edge` default, `whisper` fallback.

### Stage 4.5 — Beat timing resolver (deterministic, NEW)
- Runs AFTER voiceover, BEFORE composite.
- Input: `word_timestamps` + `beats[].trigger_phrase` per scene.
- Logic: substring match (exact → fuzzy Jaccard fallback) trigger_phrase against the running word_timestamps text → determine `start_sec` for each beat.
- Fallback: if match fails → divide duration evenly across beats (no worse than the old design).
- Output: `beats[].start_sec`, `beats[].duration_sec` per beat.

### Stage 5 — Composite + subtitle (deterministic, MPT)
- **Beat-aware compositing:** if scene has `beats_timed`, divide the Manim clip into N segments (one per beat), speed-adjust each segment to match `beat.duration_sec` from narration timing.
- If total durations are close (±15%), use uniform speed change (simple, high quality).
- If large difference, per-beat time-stretch with clamp 0.5x–2.0x (outside range → freeze/trim).
- Timeline assembler: arrange clips by `order`, render subtitles from `word_timestamps` using Pillow.
- MoviePy 2.x. No LLM in the assembly loop.

### Stage 6 — Render (deterministic, MPT)
- Mux video + audio + subtitles + BGM → encode (H.264/H.265) via ffmpeg.
- Small separate agent generates title/description/tags/thumbnail for YouTube.

---

## 6. Manim Self-Repair Loop (core — MPT does not have this)

```python
def render_scene(scene, max_repairs=4, n_variants=1):
    history = []
    for variant in range(n_variants):
        code = llm_generate_manim(scene.visual_spec, scene.narration)
        for attempt in range(max_repairs + 1):
            result = sandbox_exec(code)          # Docker, no network, cpu/mem/timeout limits
            if result.error:
                if attempt == max_repairs:
                    break                        # discard this variant
                code = llm_repair(code, traceback=result.traceback)  # ReAct loop
                continue
            frames = sample_frames(result.clip)  # representative frames
            qa = vision_qa(frames, intent=scene.visual_spec)         # matches intent?
            if qa.passed:
                return Clip(path=result.clip, qa_passed=True, code=code)
            code = llm_repair(code, feedback=qa.issues)  # visual error, not runtime error
    return best_effort_or_flag_for_human()
```

Constraints:
- `max_repairs` cap (3–5) to avoid unlimited token burn.
- Sandbox required: Docker with Manim pre-installed, **no network**, hard timeout, CPU/RAM limits.
- Distinguish 2 failure types: **runtime errors** (fix via traceback) vs **visual errors** (fix via vision QA feedback). The second type is the hard one.
- Cache by `manim_code_hash`: if code is unchanged, do not re-render.

---

## 7. Orchestration, State, Storage

- **Front half (agentic + HITL):** stateful graph with interrupt — LangGraph. Pause at checkpoints, resume after approval.
- **Heavy media steps (Manim render, final render):** job queue + worker (Celery/RQ) or durable execution (Temporal). Long-running, failure-prone → do not run inline in a request, avoid losing work on crash.
- **State:** Postgres, `Project` entity with per-stage status. Artifacts referenced by path.
- **Artifacts:** object storage (S3) or local fs for clips/audio/scripts.
- **Cache:** content-hash (see D5).
- **Observability:** trace LLM tokens + render-minutes + API cost per project. Real costs are in stages 3 and 6.
- **HITL UI:** Streamlit (reused from MPT).

---

## 8. Tech Stack

- **Language:** Python 3.11 (matching MPT), env managed by `uv`.
- **Agent/orchestration:** LangGraph (front half) + Celery/RQ or Temporal (media jobs).
- **Animation:** Manim Community Edition, running in Docker sandbox.
- **Media:** MoviePy 2.x, Pillow (subtitles), ffmpeg.
- **TTS:** Edge TTS (free, default) / Azure Speech (paid). Transcription fallback: faster-whisper.
- **Stock (optional):** Pexels API.
- **LLM:** abstraction over multiple providers via config (OpenAI/Anthropic/Gemini/DeepSeek/Ollama...).
- **Vision QA:** vision-capable LLM.
- **State:** Postgres. **Storage:** S3/local. **UI:** Streamlit + FastAPI.
- **Deploy:** Docker / docker-compose (CPU; GPU optional for whisper + faster render).

---

## 9. Conventions & Constraints for Coding Agents

- DO NOT let LLMs generate or control individual ffmpeg or MoviePy commands. LLM decides *what*; deterministic code *executes* the how.
- DO NOT run Manim code outside the sandbox.
- DO NOT finalize the timeline before `duration_sec` is available from stage 4.
- DO NOT embed binaries (clips/audio) in `VideoSpec` or Postgres — paths only.
- DO NOT place voiceover after edit.
- Each stage may only write its own fields in `VideoSpec`.
- All LLM/TTS/stock calls go through the provider abstraction layer, never hardcoded SDKs in pipeline logic.
- Cap repair iterations; any scene that exceeds the cap must be flagged for human review — do not silently render a bad clip.
- Idempotent by content-hash at every render step.

---

## 10. Suggested Directory Structure

```
app/
  agents/        # market_search, script, manim_codegen (LLM-facing)
  pipeline/      # voiceover, composite, render (deterministic)
  sandbox/       # Docker runner for Manim
  providers/     # llm/, tts/, stock/ — abstraction + config
  models/        # VideoSpec, Project (pydantic + ORM)
  orchestration/ # LangGraph graph + queue tasks
  templates/     # parametrized Manim template library
webui/           # Streamlit (HITL review)
api/             # FastAPI
resource/        # songs/, fonts/
```

---

## 11. Known Risks (read carefully)

1. **Manim codegen** — quality risk, not just errors. Code runs but objects overlap, formulas overflow the frame. → Parametrized templates + visual QA + batch-and-pick.
2. **Narration ↔ animation sync** — what separates a good explainer from a slideshow. 3Blue1Brown does this manually. Automating "animation plays exactly when narration mentions it" is the hardest core problem. Do not expect v1 to do this well.
   - **Mitigation (beat system):** trigger_phrase matching + per-beat time-stretching resolves ~70% of cases. The remaining 30% (animation pacing within a beat) needs human review or rate_func tuning.
3. **Intersection of "trending" and "visualizable"** is narrow — the market agent must filter for feasibility, otherwise it will suggest topics that are impossible to animate.
4. **Cost** — stages 3 and 6 burn the most (render + repair tokens). Cache + repair cap are mandatory.
5. **Stock for math/physics** — mostly useless, makes the video feel cheap. Keep minimal.

---

## 12. Suggested Build Order

1. Define `VideoSpec` + Project state (Postgres) before everything else — this is the backbone.
2. Plug in the media layer from MPT: voiceover → composite → render (deterministic pipeline running with dummy clips).
3. Manim sandbox + render 1 scene from hand-written code → verify clip interface plugs into compositor.
4. Manim codegen + self-repair loop + visual QA.
5. Parametrized template library.
6. Script agent → emit VideoSpec.
7. Market search agent + HITL UI (Streamlit).
8. LangGraph orchestration connecting everything + queue for media jobs.
