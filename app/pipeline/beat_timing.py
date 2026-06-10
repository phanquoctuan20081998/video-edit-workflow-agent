"""Beat timing resolver — matches beats to word timestamps.

After TTS (Stage 4), each scene has word_timestamps. This module maps each beat's
start time within the audio, enabling intra-scene sync.

Strategy (in priority order):
  1. narration_segment exact match — the full beat text, unambiguous, long
  2. trigger_phrase exact match — shorter, may be ambiguous
  3. trigger_phrase fuzzy (token Jaccard, threshold 0.7)
  4. Fallback: distribute beats equally across scene duration

Fallback: if matching fails, distribute beats equally across scene duration.
"""

from __future__ import annotations

import re

import structlog

from app.models.video_spec import Beat, Scene, VideoSpec, WordTimestamp

log = structlog.get_logger()

# Strip punctuation so narration text ("Vậy, điều gì xảy ra?") matches the TTS
# word stream ("Vậy điều gì xảy ra"). Mismatched punctuation was the #1 cause
# of beats falling back to equal distribution (= audio/visual desync).
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    return re.sub(r"\s+", " ", _PUNCT_RE.sub(" ", text.lower())).strip()


def resolve_beat_timing(spec: VideoSpec) -> VideoSpec:
    """Resolve beat timing for all scenes that have beats and word timestamps."""
    for scene in spec.scenes:
        if not scene.has_beats or not scene.word_timestamps:
            continue
        if scene.beats_timed:
            log.info("beat_timing.skip_already_timed", scene_id=scene.id)
            continue
        _resolve_scene_beats(scene)
    return spec


def _resolve_scene_beats(scene: Scene) -> None:
    """Match beats to word timestamps within a single scene."""
    words = scene.word_timestamps
    if not words:
        return

    # Build character-to-time mapping from NORMALIZED word timestamps
    char_positions = _build_char_positions(words)
    full_text = " ".join(_normalize(w.word) for w in words if _normalize(w.word))

    beats_sorted = sorted(scene.beats, key=lambda b: b.order)
    matched_starts: list[float | None] = []

    for beat in beats_sorted:
        start_sec = _find_beat_onset(beat, full_text, char_positions)
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

    We reconstruct the full text as space-joined NORMALIZED words and track
    character offsets. Punctuation-only tokens are skipped so offsets line up
    with the normalized full_text used for matching.
    """
    positions = []
    char_offset = 0
    for w in words:
        word_norm = _normalize(w.word)
        if not word_norm:
            continue  # punctuation-only token
        char_start = char_offset
        char_end = char_offset + len(word_norm)
        positions.append((char_start, char_end, w.start, w.end))
        char_offset = char_end + 1  # +1 for the space
    return positions


def _find_beat_onset(
    beat: Beat,
    full_text: str,
    char_positions: list[tuple[int, int, float, float]],
) -> float | None:
    """Find the start time of a beat in the word timeline.

    Priority:
    1. narration_segment exact match (full beat text — long, unique, most reliable)
    2. trigger_phrase exact match
    3. trigger_phrase fuzzy token overlap (Jaccard >= 0.7)
    """
    # Strategy 1: narration_segment exact match (primary — VideoAgent-inspired)
    segment_norm = _normalize(beat.narration_segment)
    if segment_norm:
        # Use first N words of the segment (up to ~8 words) for matching,
        # since TTS may paraphrase slightly at segment boundaries
        segment_words = segment_norm.split()
        anchor = " ".join(segment_words[:min(8, len(segment_words))])
        idx = full_text.find(anchor)
        if idx != -1:
            return _char_offset_to_time(idx, char_positions)

    # Strategy 2: trigger_phrase exact match
    return _find_phrase_onset(beat.trigger_phrase, full_text, char_positions)


def _find_phrase_onset(
    trigger_phrase: str,
    full_text: str,
    char_positions: list[tuple[int, int, float, float]],
) -> float | None:
    """Find the start time of a trigger phrase in the word timeline."""
    phrase_norm = _normalize(trigger_phrase)
    if not phrase_norm:
        return None

    # Exact substring
    idx = full_text.find(phrase_norm)
    if idx != -1:
        return _char_offset_to_time(idx, char_positions)

    # Fuzzy token overlap (raised threshold: 0.7)
    return _fuzzy_match_onset(phrase_norm, full_text, char_positions)


def _char_offset_to_time(
    char_offset: int,
    char_positions: list[tuple[int, int, float, float]],
) -> float | None:
    """Convert a character offset in the full text to a time value."""
    for char_start, char_end, time_start, _ in char_positions:
        if char_start <= char_offset < char_end:
            return time_start
        if char_offset == char_end:
            continue
    if char_positions:
        return char_positions[-1][2]
    return None


def _fuzzy_match_onset(
    phrase: str,
    full_text: str,
    char_positions: list[tuple[int, int, float, float]],
    threshold: float = 0.7,
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
