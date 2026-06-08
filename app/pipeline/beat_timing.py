"""Beat timing resolver — matches trigger phrases to word timestamps.

After TTS (Stage 4), each scene has word_timestamps. This module maps each beat's
trigger_phrase to a start time within the audio, enabling intra-scene sync.

Strategy:
  1. Concatenate word timestamps into running text with positions
  2. Find trigger_phrase onset via substring match (exact first, fuzzy fallback)
  3. Assign each beat: start_sec, duration_sec

Fallback: if matching fails, distribute beats equally across scene duration.
"""

from __future__ import annotations

import structlog

from app.models.video_spec import Beat, Scene, VideoSpec, WordTimestamp

log = structlog.get_logger()


def resolve_beat_timing(spec: VideoSpec) -> VideoSpec:
    """Resolve beat timing for all scenes that have beats and word timestamps."""
    for scene in spec.scenes:
        if not scene.has_beats or not scene.word_timestamps:
            continue
        _resolve_scene_beats(scene)
    return spec


def _resolve_scene_beats(scene: Scene) -> None:
    """Match beats to word timestamps within a single scene."""
    words = scene.word_timestamps
    if not words:
        return

    # Build character-to-time mapping from word timestamps
    char_positions = _build_char_positions(words)
    full_text = " ".join(w.word for w in words).lower()

    beats_sorted = sorted(scene.beats, key=lambda b: b.order)
    matched_starts: list[float | None] = []

    for beat in beats_sorted:
        start_sec = _find_phrase_onset(beat.trigger_phrase, full_text, char_positions)
        matched_starts.append(start_sec)

    # Fill unmatched beats and compute durations
    _fill_and_compute_durations(beats_sorted, matched_starts, scene.duration_sec)

    log.info(
        "beat_timing.resolved",
        scene_id=scene.id,
        beats=len(beats_sorted),
        matched=sum(1 for s in matched_starts if s is not None),
    )


def _build_char_positions(words: list[WordTimestamp]) -> list[tuple[int, int, float, float]]:
    """Build mapping: (char_start, char_end, time_start, time_end) for each word.

    We reconstruct the full text as space-joined words and track character offsets.
    """
    positions = []
    char_offset = 0
    for w in words:
        word_lower = w.word.lower()
        char_start = char_offset
        char_end = char_offset + len(word_lower)
        positions.append((char_start, char_end, w.start, w.end))
        char_offset = char_end + 1  # +1 for the space
    return positions


def _find_phrase_onset(
    trigger_phrase: str,
    full_text: str,
    char_positions: list[tuple[int, int, float, float]],
) -> float | None:
    """Find the start time of a trigger phrase in the word timeline.

    Strategy 1: Exact substring match (fast, works for most cases).
    Strategy 2: Token overlap sliding window (handles minor TTS differences).
    """
    phrase_lower = trigger_phrase.lower().strip()
    if not phrase_lower:
        return None

    # Strategy 1: Exact substring
    idx = full_text.find(phrase_lower)
    if idx != -1:
        return _char_offset_to_time(idx, char_positions)

    # Strategy 2: Token-level sliding window with Jaccard similarity
    return _fuzzy_match_onset(phrase_lower, full_text, char_positions)


def _char_offset_to_time(
    char_offset: int,
    char_positions: list[tuple[int, int, float, float]],
) -> float | None:
    """Convert a character offset in the full text to a time value."""
    for char_start, char_end, time_start, _ in char_positions:
        if char_start <= char_offset < char_end:
            return time_start
        # Character falls in a space between words — use next word's start
        if char_offset == char_end:
            continue
    # Past all words — return last word's start
    if char_positions:
        return char_positions[-1][2]
    return None


def _fuzzy_match_onset(
    phrase: str,
    full_text: str,
    char_positions: list[tuple[int, int, float, float]],
    threshold: float = 0.5,
) -> float | None:
    """Sliding window token overlap match for fuzzy phrase finding."""
    phrase_tokens = set(phrase.split())
    if not phrase_tokens:
        return None

    words_in_text = full_text.split()
    window_size = len(phrase.split())
    best_score = 0.0
    best_char_offset = None

    char_offset = 0
    for i in range(len(words_in_text) - window_size + 1):
        window = set(words_in_text[i : i + window_size])
        # Jaccard similarity
        intersection = len(phrase_tokens & window)
        union = len(phrase_tokens | window)
        score = intersection / union if union > 0 else 0.0

        if score > best_score:
            best_score = score
            best_char_offset = char_offset

        char_offset += len(words_in_text[i]) + 1

    if best_score >= threshold and best_char_offset is not None:
        return _char_offset_to_time(best_char_offset, char_positions)

    return None


def _fill_and_compute_durations(
    beats: list[Beat],
    matched_starts: list[float | None],
    scene_duration: float,
) -> None:
    """Fill missing start times and compute duration for each beat.

    Rules:
    - Beats must be monotonically increasing in start time
    - Unmatched beats are interpolated between matched neighbors
    - Last beat extends to scene end
    """
    n = len(beats)
    if n == 0 or scene_duration is None:
        return

    # Ensure monotonicity: if a matched start is earlier than previous, discard it
    for i in range(1, n):
        if matched_starts[i] is not None and matched_starts[i - 1] is not None:
            if matched_starts[i] <= matched_starts[i - 1]:
                matched_starts[i] = None

    # Fallback: if nothing matched, distribute equally
    if all(s is None for s in matched_starts):
        log.warning("beat_timing.fallback_equal_distribution", n_beats=n)
        segment = scene_duration / n
        for i, beat in enumerate(beats):
            beat.start_sec = i * segment
            beat.duration_sec = segment
        return

    # First beat defaults to 0.0 if unmatched
    if matched_starts[0] is None:
        matched_starts[0] = 0.0

    # Interpolate gaps
    starts: list[float] = []
    for i in range(n):
        if matched_starts[i] is not None:
            starts.append(matched_starts[i])
        else:
            # Find next matched
            prev_val = starts[-1] if starts else 0.0
            next_val = scene_duration
            next_idx = n
            for j in range(i + 1, n):
                if matched_starts[j] is not None:
                    next_val = matched_starts[j]
                    next_idx = j
                    break
            # Linear interpolation
            gap_count = next_idx - (i - 1) if starts else next_idx - i + 1
            step = (next_val - prev_val) / gap_count
            starts.append(prev_val + step * (i - len(starts) + 1))

    # Assign start_sec and duration_sec
    for i, beat in enumerate(beats):
        beat.start_sec = starts[i]
        if i < n - 1:
            beat.duration_sec = starts[i + 1] - starts[i]
        else:
            beat.duration_sec = scene_duration - starts[i]

        # Clamp to non-negative
        if beat.duration_sec < 0:
            beat.duration_sec = 0.0
