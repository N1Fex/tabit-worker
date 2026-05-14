from __future__ import annotations

from tabit_worker.fretboard import choose_positions
from tabit_worker.models import DetectedNote, QuantizedNote


GRID_STEP = 0.25
REST_GAP_THRESHOLD = 1.0


def quantize_notes(notes: list[DetectedNote], bpm: float) -> list[QuantizedNote]:
    if bpm <= 0:
        raise ValueError("BPM must be positive.")
    if not notes:
        return []

    beat_seconds = 60.0 / bpm
    raw_starts = [note.start_time / beat_seconds for note in notes]
    raw_ends = [note.end_time / beat_seconds for note in notes]
    quantized_starts = _quantize_starts(raw_starts)
    positions = choose_positions([note.midi for note in notes])

    quantized: list[QuantizedNote] = []
    for index, note in enumerate(notes):
        beats = _resolve_note_duration(index, raw_starts, raw_ends, quantized_starts)
        position = positions[index]
        quantized.append(
            QuantizedNote(
                midi=note.midi,
                start_beat=quantized_starts[index],
                beats=beats,
                frequency=note.frequency,
                string=position.string,
                fret=position.fret,
            )
        )

    return quantized


def _quantize_starts(raw_starts: list[float]) -> list[float]:
    starts: list[float] = []
    initial_gap = raw_starts[0]
    starts.append(_quantize_gap(initial_gap) if initial_gap >= REST_GAP_THRESHOLD else 0.0)

    for index in range(1, len(raw_starts)):
        onset_interval = max(GRID_STEP, raw_starts[index] - raw_starts[index - 1])
        starts.append(starts[-1] + _snap_to_grid(onset_interval, minimum=GRID_STEP))

    return starts


def _resolve_note_duration(
    index: int,
    raw_starts: list[float],
    raw_ends: list[float],
    quantized_starts: list[float],
) -> float:
    raw_duration = max(GRID_STEP, raw_ends[index] - raw_starts[index])

    if index == len(raw_starts) - 1:
        return _snap_to_grid(raw_duration, minimum=GRID_STEP)

    next_start = quantized_starts[index + 1]
    current_start = quantized_starts[index]
    onset_gap = max(GRID_STEP, next_start - current_start)
    raw_silence = max(0.0, raw_starts[index + 1] - raw_ends[index])

    if raw_silence < REST_GAP_THRESHOLD:
        return onset_gap

    rest_beats = _quantize_gap(raw_silence)
    sustaining_beats = max(GRID_STEP, onset_gap - rest_beats)
    own_duration = _snap_to_grid(raw_duration, minimum=GRID_STEP)
    return min(max(own_duration, GRID_STEP), sustaining_beats)


def _quantize_gap(raw_beats: float) -> float:
    return _snap_to_grid(raw_beats, minimum=GRID_STEP)


def _snap_to_grid(raw_beats: float, minimum: float) -> float:
    snapped = round(raw_beats / GRID_STEP) * GRID_STEP
    return max(minimum, snapped)
