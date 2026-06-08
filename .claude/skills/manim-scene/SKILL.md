---
name: manim-scene
description: >
  Generate, repair, and QA a Manim scene for the video-edit-workflow-agent pipeline.
  Trigger when: writing Manim code for a scene, fixing Manim render errors, reviewing
  a generated scene, running visual QA on Manim output, using app/templates/, or any
  task involving manim_code or clip_path fields in VideoSpec.
type: task
---

# manim-scene — Manim Codegen, Repair & QA

## 3Blue1Brown visual design system

Every generated scene MUST start with this header (defined in `app/templates/base.py:STYLE_HEADER`):

```python
from manim import *
import numpy as np

BACKGROUND_COLOR = "#1C1C2E"  # deep navy — never pure black
P_BLUE   = "#58C4DD"   # primary objects
P_GREEN  = "#83C167"   # secondary objects
P_YELLOW = "#FFFF00"   # emphasis / final answer
P_GOLD   = "#C49A04"   # softer emphasis
P_RED    = "#FC6255"   # negation / error
P_TEAL   = "#49A88F"   # transforms / in-between state
P_WHITE  = "#FFFFFF"   # ALL text and formulas
P_GREY   = "#BDBDBD"   # secondary labels
P_AXIS   = "#1C758A"   # axes, grids (subdued)
P_DIM    = "#55534E"   # dashed construction lines
```

First line of `construct()`: `self.camera.background_color = BACKGROUND_COLOR`

### Color semantics
Colors carry meaning. Same color = same concept across entire scene.

| Color | Semantic |
|-------|----------|
| P_BLUE | Primary object — vectors, main curve, key shape |
| P_GREEN | Secondary / supporting object |
| P_YELLOW | Final result, peak emphasis — use sparingly |
| P_RED | Negation, cancellation, removal |
| P_AXIS | Axes, NumberPlane, grid — never dominant |
| P_DIM | DashedLine, construction aids |
| P_WHITE | All text and MathTex |

Never assign colors arbitrarily. Viewer infers same color = same concept.

### Typography rules
- `MathTex` for ALL math — never `Text("f(x) = x^2")`
- After creating MathTex: `if tex.width > 10: tex.scale(10 / tex.width)`
- Title: `Text("...", font_size=40, color=P_WHITE).to_edge(UP, buff=0.5)`
- Labels: `.scale(0.65)`, `next_to(obj, direction, buff=0.25)`

### Layout rules
- No overlaps — `VGroup(a, b, c).arrange(DOWN, buff=0.75)`
- Never hardcode shifts — use `to_edge()`, `next_to()`, `move_to()`
- Frame = 14.22 × 8.0 units. Nothing within 0.5 units of edge.
- Max 6–8 objects simultaneously. More → `FadeOut` old ones first.

### Animation rhythm
```
Write(tex)                  ← formulas drawn stroke-by-stroke (ALWAYS for math)
Create(shape)               ← shapes traced along path
FadeIn(obj)                 ← background context only, NEVER for hero math
Transform(A, B)             ← A becomes B — core 3b1b technique
Indicate(obj)               ← pulse focus without moving
Circumscribe(obj)           ← draw circle to highlight
SurroundingRectangle(obj, color=P_YELLOW)  ← box a key term
```

Pacing:
- `self.wait(1.0)` after every major reveal
- `self.wait(0.5)` between minor beats
- `run_time=1.0` standard; `1.5–2.0` for transforms; `0.5` for minor highlights
- **Never `self.wait(0)`**

### Anti-patterns (AI slop — QA will fail these)
- `self.add(a, b, c, d)` — all objects appear at once with no animation
- `FadeIn(equation)` — use `Write(equation)`
- Objects that appear and never move, highlight, or change
- Random colors with no semantic assignment
- `Text()` for math expressions
- Hardcoded `.shift(3.14159)`
- Pure black background
- 8+ equations visible simultaneously

---

## Version constraint

Manim **Community Edition** only. Never use `manimlib` (3Blue1Brown's fork).
Target version pinned in `requirements.txt`. Check before writing API calls.

Key CE vs manimlib differences that break at runtime:
- `Scene.construct()` not `__init__`
- `self.play()` takes `Animation` objects, not methods
- `Write`, `Create`, `FadeIn`, `Transform` — CE names. `ShowCreation` is manimlib, use `Create`
- `MathTex` for LaTeX, `Tex` for text with LaTeX, `Text` for plain text
- `NumberPlane` not `ComplexPlane` for generic grids
- `VGroup` for grouping, `.arrange()` for layout

## Template library (app/templates/)

**Prefer parametrized templates over free-form code.** Trade flexibility for reliability.

Available template categories:
- `vectors.py` — vector field, arrow groups, dot product visualization
- `functions.py` — function graph with highlight region, derivative tangent
- `geometry.py` — polygon transforms, rotation, reflection
- `charts.py` — bar chart, pie chart, number line with markers
- `signal.py` — waveform, frequency spectrum (FFT visualization)
- `matrix.py` — matrix multiplication step-by-step, eigenvalue transform

Usage pattern — LLM fills params, does NOT write render logic:
```python
from app.templates.vectors import VectorSumScene

scene = VectorSumScene(
    vectors=[(1, 2), (3, -1), (-2, 1)],
    show_components=True,
    highlight_resultant=True,
    narration_beat="sum of three vectors"
)
```

When to write free-form: scene needs animation not covered by any template,
OR template would require >3 custom overrides to fit.
Document why in a comment.

## Sandbox constraints

Sandbox = Docker container, Manim CE installed, **no network**.
- No `urllib`, `requests`, `httpx`, or any network import
- No reading files outside `/workspace/` (mounted scene dir)
- No `subprocess`, `os.system`, `eval`, `exec` on user data
- Timeout: **90 seconds** per scene (hard kill). Animations >90s wall time fail.
- CPU: 2 cores. RAM: 2 GB. No GPU (CPU renderer only).

For long animations: use `run_time` param to keep total ≤ 80s.
Prefer `rate_func=linear` over `smooth` for predictable timing.

## VideoSpec field contract

Stage 3 (codegen) writes ONLY:
- `scene.manim_code` — generated Python source
- `scene.manim_code_hash` — sha256 of manim_code (cache key)
- `scene.clip_path` — path to rendered .mp4 (relative to project dir)
- `scene.clip_qa_passed` — bool, set by vision QA

Never touch: `duration_sec`, `audio_path`, `word_timestamps` (stage 4 owns).

## Self-repair loop

```python
# max_repairs=4, escalate to human after
for attempt in range(max_repairs + 1):
    result = sandbox_exec(code)
    if result.error:
        if attempt == max_repairs: flag_for_human(scene); break
        code = llm_repair(code, traceback=result.traceback)
        continue
    frames = sample_frames(result.clip, n=4)
    qa = vision_qa(frames, intent=scene.visual_spec)
    if qa.passed: return success
    code = llm_repair(code, feedback=qa.issues)
```

Two distinct repair paths — never conflate:
1. **Runtime error** → pass traceback, ask for fix to make it run
2. **Visual QA fail** → pass frame screenshots + qa.issues, ask for visual fix

Repair prompt must include the FULL current code, not a diff.

## Visual QA criteria

Vision model checks frames against `scene.visual_spec`. Pass requires ALL:
- [ ] Intended objects present (no missing elements)
- [ ] No overlapping text/objects (layout readable)
- [ ] No LaTeX render errors (? boxes, missing symbols)
- [ ] No objects cropped by frame boundary
- [ ] Color contrast sufficient (dark bg default)
- [ ] Animation completes before clip ends (no freeze mid-motion)

QA prompt template:
```
You are reviewing a Manim animation frame.
Intent: {scene.visual_spec}
Narration context: {scene.narration}
Does this frame correctly represent the intent? List any visual problems.
Output JSON: {"passed": bool, "issues": [str]}
```

## Common pitfalls

**Overlapping objects**
- Always `VGroup(...).arrange(DOWN, buff=0.4)` or set explicit `.shift()`
- MathTex default position = ORIGIN. Multiple formulas without `.arrange()` stack at center.
- Fix: `group = VGroup(eq1, eq2, eq3).arrange(DOWN, buff=0.5).move_to(ORIGIN)`

**Formula overflow**
- Long `MathTex` strings exceed frame width at default scale
- Fix: `.scale(0.7)` or split into multiple lines with `r"\\"` in LaTeX
- Check: object width should be `< 12` (frame is 14.22 units wide)

**Wrong animation on Mobject type**
- `Write` only works on `VMobject` (Text, MathTex, shapes). Not on `ImageMobject`.
- `FadeIn`/`FadeOut` works on all Mobjects.
- `Create` for stroke-based shapes, `Write` for text.

**Scene not rendering (blank output)**
- Must call `self.play()` or `self.add()` + `self.wait()` inside `construct()`
- `self.wait(0)` renders blank. Minimum `self.wait(0.5)`.

**Camera/frame mismatch**
- Default frame: 1920×1080. For portrait (Shorts): use `config.frame_width = 9; config.frame_height = 16`
- Set in scene class: `camera_config = {"frame_width": 9, "frame_height": 16}`

**Manim CE version API drift**
- `FunctionGraph` → `axes.plot(lambda x: ...)` in CE ≥0.18
- `get_graph` → deprecated, use `Axes.plot()`
- `NumberPlane.get_vector()` → use `Arrow(plane.c2p(0,0), plane.c2p(*vec))`

## Caching

Compute hash before any render:
```python
import hashlib
scene.manim_code_hash = hashlib.sha256(scene.manim_code.encode()).hexdigest()
```
If hash matches existing `clip_path` that exists on disk → skip render, reuse clip.
Log cache hits. Cache miss rate should drop after template adoption.
