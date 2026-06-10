# Pipeline Improvements — June 2026

Targeted fixes for the five reported issues. All changes verified with smoke tests.

## 1. Manim code is wrong too often

**`app/agents/manim_codegen.py`**

- **manimlib → CE auto-translation** (`_fix_manimlib_api`): `ShowCreation`→`Create`,
  `TextMobject`→`Text`, `TexMobject`→`MathTex`, `.get_graph(`→`.plot(`, etc.
  Old manimlib API leaking from LLM training data was the biggest source of
  AttributeError/NameError repair cycles — now fixed for free, no LLM round-trip.
- **New pre-checks** (run before the expensive 90s docker render):
  - `_undefined_name_check` — AST walk that catches NameErrors statically
    (resolves against the real `manim` + `numpy` namespaces; auto-skips if
    manim isn't importable in the host process).
  - `_manimlib_pattern_check` — rejects `GraphScene` subclassing and
    `CONFIG = {...}` dicts (manimlib-only patterns that can't be auto-fixed)
    with targeted repair instructions.
- **Expanded `_ERROR_HINTS`**: IndexError, ZeroDivisionError, ShowCreation,
  TextMobject, get_graph, GraphScene, CONFIG.
- **`_fix_zero_waits`**: rewrites `self.wait(0)` to `self.wait(0.5)`.

## 2. Animations not beautiful / clean

- **Programmatic static-scene gate** (`app/sandbox/frame_sampler.py::motion_score`
  + gate in `render_scene`): computes mean inter-frame pixel difference on the
  sampled frames. Near-identical frames (the "static slide" slop mode) auto-fail
  with precise repair feedback **without spending a vision-QA call**.
- **Golden mini-example** added to the codegen system prompt — a complete short
  scene demonstrating the desired rhythm (Write title → Create → wait →
  plot → Indicate), which anchors style much better than rules alone.
- **Polish rules** added: `LaggedStart` for group reveals, focal dimming with
  `.animate.set_opacity(0.3)`, subdued `axis_config`, FadeOut-with-shift on
  content replacement, persistent title.

## 3. Voiceover doesn't match the visuals

- **Root-cause bug fix** in `app/pipeline/beat_timing.py`: beat anchors from the
  narration contain punctuation ("Vậy, điều gì xảy ra?") but the TTS word stream
  doesn't ("Vậy điều gì xảy ra"), so exact matching almost always failed and the
  resolver silently fell back to **equal distribution** — i.e. beats were not
  actually synced to the narration. Both sides are now punctuation-normalized
  (Unicode-aware), and char→time offsets are built on the normalized stream.
- **Whisper recovery** in `app/pipeline/voiceover.py`: when cached audio exists
  but `word_timestamps` were lost (reloaded project), beats previously could
  never sync. Now `faster-whisper` (already in your deps) transcribes the audio
  to recover word-level timestamps. Best-effort: silently skipped if unavailable.
- **Sync at the source**: the codegen prompt now includes a per-beat
  "Target animation time: ~Xs" derived from the narration word count at the
  language's speaking rate, so generated `run_time`/`wait` values roughly match
  the voiceover and post-hoc speed-warping in compositing stays small.

## 4. Cannot control video length

- **`VideoSpec.target_duration_sec`** (new field) + `estimated_duration_sec()`
  helper + per-language words-per-minute table (`_LANG_WPM`, `words_per_second`).
- **Script agent** (`app/agents/script.py`):
  - `run()/outline()/write_spec()` accept `target_duration_sec`.
  - Outline and refine prompts now include a hard **word budget**
    (`target × WPM(language)`, ±10%).
  - New **`_fit_to_duration()`** pass: after the spec is written, if the
    estimated runtime drifts >20% from target, one LLM pass rewrites narration
    to fit while preserving beat structure and re-deriving trigger phrases.
- **UI**: target-length slider on the Script page (1–15 min); estimated duration
  shown on the draft header with a drift warning; per-scene `~Xs` estimates in
  each scene expander.

## 5. WebUI not user friendly

- **Workflow page** (`webui/pages/workflow.py`): no longer a passive diagram —
  shows a **"Next step"** instruction derived from project status with a one-click
  **"Go to <stage> →"** button, plus a per-scene status table
  (animation ✅/⚠️, voice ✅, duration, beat count).
- **Script page** (`webui/pages/script_review.py`): target-length control,
  live duration estimate + drift feedback, richer scene headers.

## Files changed

```
app/agents/manim_codegen.py      auto-fixes, pre-checks, motion gate, prompts
app/agents/script.py             duration budget, fit-to-duration pass
app/models/video_spec.py         target_duration_sec, duration estimation, WPM
app/pipeline/beat_timing.py      punctuation-normalized beat matching (bug fix)
app/pipeline/voiceover.py        faster-whisper timestamp recovery
app/sandbox/frame_sampler.py     motion_score()
webui/pages/script_review.py     length slider, duration display
webui/pages/workflow.py          guided next-step CTA, scene status table
```

## Notes

- No new hard dependencies: `faster-whisper`, `Pillow`, `numpy` are already in
  your environment; all new uses degrade gracefully if missing.
- The whisper model (`small`, int8, CPU) loads lazily once per process; the
  first recovery takes ~30s to download/load, after which it's cached.
- `_MIN_MOTION_SCORE = 1.5` (mean pixel diff, 0–255 scale) is the static-scene
  threshold; raise it if static slides still slip through.
