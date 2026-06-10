"""VideoSpec — intermediate representation backbone of the pipeline.

Every stage reads/writes only its own fields. See CLAUDE.md §4 for the contract.

Architecture: Scenes are multi-beat "chapters" (1-3 min each). Each beat is one
visual transition within a continuous Manim animation. Beats enable intra-scene
sync between narration and animation without hard cuts between related concepts.
"""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class VisualType(str, Enum):
    manim = "manim"
    stock = "stock"
    chart = "chart"
    static_image = "static_image"
    title_card = "title_card"


class ProjectStatus(str, Enum):
    searching = "searching"
    searched = "searched"
    scripted = "scripted"
    approved = "approved"
    animated = "animated"
    voiced = "voiced"
    composited = "composited"
    rendered = "rendered"


class BGM(BaseModel):
    path: Optional[str] = None
    volume: float = 0.15


class SubtitleStyle(BaseModel):
    font: str = "NotoSansCJK"
    size: int = 42
    color: str = "#FFFFFF"
    stroke: bool = True


class WordTimestamp(BaseModel):
    word: str
    start: float
    end: float


class Beat(BaseModel):
    """One visual transition within a scene. Beat = atomic visual idea.

    The trigger_phrase anchors this beat to a moment in the narration via word
    timestamps after TTS. The visual_action tells Manim codegen what animation
    to play for this beat.
    """

    id: str
    order: int
    trigger_phrase: str  # substring of narration that starts this beat
    visual_action: str  # describes the Manim animation for this beat
    narration_segment: str  # the portion of narration this beat covers
    must_show: list[str] = Field(default_factory=list)
    on_screen_label: Optional[str] = None
    forbidden_visuals: list[str] = Field(default_factory=list)

    # Filled by beat timing resolver (after voiceover)
    start_sec: Optional[float] = None
    duration_sec: Optional[float] = None

    # Filled by split-render strategy (optional per-beat clip)
    clip_path: Optional[str] = None


class Scene(BaseModel):
    id: str
    order: int
    narration: str
    visual_type: VisualType = VisualType.manim
    visual_spec: str = ""

    # Beat layer — intra-scene sync points (Stage 2 script agent sets these)
    beats: list[Beat] = Field(default_factory=list)

    # Stage 3 — Manim codegen (generates ONE continuous scene covering all beats)
    manim_code: Optional[str] = None
    manim_code_hash: Optional[str] = None
    clip_path: Optional[str] = None
    clip_qa_passed: Optional[bool] = None
    beat_render_durations: Optional[list[float]] = None  # parsed from manim code per beat

    # Stage 4 — Voiceover (source of truth for timeline)
    audio_path: Optional[str] = None
    duration_sec: Optional[float] = None
    word_timestamps: Optional[list[WordTimestamp]] = None

    # Stock (optional)
    stock_query: Optional[str] = None
    stock_clip_path: Optional[str] = None

    def set_manim_code(self, code: str) -> None:
        """Stage 3 writer — updates code + hash atomically."""
        self.manim_code = code
        self.manim_code_hash = hashlib.sha256(code.encode()).hexdigest()

    def set_clip(self, clip_path: str, qa_passed: bool) -> None:
        """Stage 3 writer — sets render output."""
        self.clip_path = clip_path
        self.clip_qa_passed = qa_passed

    def set_voiceover(
        self,
        audio_path: str,
        duration_sec: float,
        word_timestamps: list[WordTimestamp],
    ) -> None:
        """Stage 4 writer — sets audio + timing. This drives the timeline."""
        self.audio_path = audio_path
        self.duration_sec = duration_sec
        self.word_timestamps = word_timestamps

    @property
    def has_beats(self) -> bool:
        return len(self.beats) > 0

    @property
    def beats_timed(self) -> bool:
        """True if all beats have timing resolved from word timestamps."""
        return self.has_beats and all(
            b.start_sec is not None and b.duration_sec is not None for b in self.beats
        )


class VideoSpec(BaseModel):
    project_id: str = Field(default_factory=lambda: str(uuid4()))
    topic: str
    source_refs: list[str] = Field(default_factory=list)
    language: str = "vi"
    aspect_ratio: str = "16:9"
    status: ProjectStatus = ProjectStatus.scripted
    scenes: list[Scene] = Field(default_factory=list)
    bgm: BGM = Field(default_factory=BGM)
    subtitle_style: SubtitleStyle = Field(default_factory=SubtitleStyle)
    final_video_path: Optional[str] = None

    # Target total video length (seconds). The script agent budgets narration
    # word counts against this; composite stage reports drift. None = no target.
    target_duration_sec: Optional[float] = None

    def get_scene(self, scene_id: str) -> Scene:
        for s in self.scenes:
            if s.id == scene_id:
                return s
        raise KeyError(f"Scene {scene_id!r} not found")

    def all_scenes_animated(self) -> bool:
        manim_scenes = [s for s in self.scenes if s.visual_type in (VisualType.manim, VisualType.chart)]
        return all(s.clip_qa_passed for s in manim_scenes)

    def all_scenes_voiced(self) -> bool:
        return all(s.duration_sec is not None for s in self.scenes)

    def estimated_duration_sec(self) -> float:
        """Estimate total runtime from narration word counts (or real TTS durations).

        Scenes already voiced use their real duration_sec. Unvoiced scenes are
        estimated from word count at a language-specific speaking rate.
        """
        total = 0.0
        wps = words_per_second(self.language)
        for s in self.scenes:
            if s.duration_sec and s.duration_sec > 0:
                total += s.duration_sec
            else:
                total += len(s.narration.split()) / wps if s.narration else 0.0
        return round(total, 1)


# Approximate TTS speaking rates (words/minute) per language. Used to budget
# narration length against target_duration_sec before any audio exists.
_LANG_WPM: dict[str, int] = {
    "en": 150, "vi": 160, "fr": 160, "de": 130, "es": 165,
    "ja": 115, "zh": 110, "ko": 120,
}


def words_per_second(language: str) -> float:
    return _LANG_WPM.get(language, 150) / 60.0
