from __future__ import annotations

import math
from statistics import median

from tabit_worker.models import DetectedNote


FRAME_MS = 40
HOP_MS = 10
MIN_FREQUENCY = 82.41
MAX_FREQUENCY = 987.77
MIN_NOTE_SECONDS = 0.08
MIN_TRANSIENT_NOTE_SECONDS = 0.12
SILENCE_RMS = 0.015
PITCH_STABILITY_SEMITONES = 0.6
SMOOTHING_WINDOW = 2
MIN_SEGMENT_FRAMES = 3
MERGEABLE_PITCH_DISTANCE = 1


def rms(frame: list[float]) -> float:
    if not frame:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in frame) / len(frame))


def frequency_to_midi(frequency: float) -> int:
    return int(round(69 + 12 * math.log2(frequency / 440.0)))


def estimate_frame_pitch(frame: list[float], sample_rate: int) -> float | None:
    energy = rms(frame)
    if energy < SILENCE_RMS:
        return None

    min_lag = max(1, int(sample_rate / MAX_FREQUENCY))
    max_lag = min(len(frame) - 1, int(sample_rate / MIN_FREQUENCY))
    if max_lag <= min_lag:
        return None

    best_lag = None
    best_score = float("-inf")
    for lag in range(min_lag, max_lag + 1):
        score = 0.0
        for index in range(len(frame) - lag):
            score += frame[index] * frame[index + lag]
        if score > best_score:
            best_score = score
            best_lag = lag

    if best_lag is None or best_score <= 0:
        return None
    return sample_rate / best_lag


def extract_notes(samples: list[float], sample_rate: int) -> list[DetectedNote]:
    frame_size = int(sample_rate * FRAME_MS / 1000)
    hop_size = int(sample_rate * HOP_MS / 1000)
    if frame_size <= 0 or hop_size <= 0:
        raise ValueError("Frame size and hop size must be positive.")

    frames: list[dict[str, float | int | None]] = []
    frame_half = frame_size / sample_rate / 2
    hop_half = hop_size / sample_rate / 2

    for start in range(0, max(1, len(samples) - frame_size + 1), hop_size):
        frame = samples[start:start + frame_size]
        if len(frame) < frame_size:
            break
        center_time = (start / sample_rate) + frame_half
        frequency = estimate_frame_pitch(frame, sample_rate)
        midi = frequency_to_midi(frequency) if frequency else None
        frames.append({"center": center_time, "midi": midi, "frequency": frequency})

    _smooth_frames(frames)
    _suppress_short_frame_runs(frames)
    notes = _frames_to_notes(frames, hop_half)
    return cleanup_notes(notes)


def cleanup_notes(notes: list[DetectedNote]) -> list[DetectedNote]:
    if not notes:
        return []

    cleaned = list(notes)
    changed = True
    while changed:
        changed = False
        next_notes: list[DetectedNote] = []
        index = 0
        while index < len(cleaned):
            note = cleaned[index]
            previous = next_notes[-1] if next_notes else None
            following = cleaned[index + 1] if index + 1 < len(cleaned) else None

            if note.duration < MIN_TRANSIENT_NOTE_SECONDS:
                if previous and following and previous.midi == following.midi:
                    next_notes[-1] = DetectedNote(
                        midi=previous.midi,
                        start_time=previous.start_time,
                        end_time=following.end_time,
                        frequency=(previous.frequency + following.frequency) / 2,
                    )
                    index += 2
                    changed = True
                    continue

                if previous and abs(note.midi - previous.midi) <= MERGEABLE_PITCH_DISTANCE:
                    next_notes[-1] = DetectedNote(
                        midi=previous.midi,
                        start_time=previous.start_time,
                        end_time=max(previous.end_time, note.end_time),
                        frequency=(previous.frequency + note.frequency) / 2,
                    )
                    index += 1
                    changed = True
                    continue

                if following and abs(note.midi - following.midi) <= MERGEABLE_PITCH_DISTANCE:
                    cleaned[index + 1] = DetectedNote(
                        midi=following.midi,
                        start_time=note.start_time,
                        end_time=following.end_time,
                        frequency=(note.frequency + following.frequency) / 2,
                    )
                    index += 1
                    changed = True
                    continue

                index += 1
                changed = True
                continue

            if previous and note.midi == previous.midi:
                next_notes[-1] = DetectedNote(
                    midi=note.midi,
                    start_time=previous.start_time,
                    end_time=note.end_time,
                    frequency=(previous.frequency + note.frequency) / 2,
                )
                changed = True
            else:
                next_notes.append(note)
            index += 1

        cleaned = next_notes

    return cleaned


def _frames_to_notes(frames: list[dict[str, float | int | None]], hop_half: float) -> list[DetectedNote]:
    notes: list[DetectedNote] = []
    current_start = None
    current_midis: list[int] = []
    current_freqs: list[float] = []
    previous_center = None

    for frame in frames:
        center = float(frame["center"])
        midi = frame["midi"]
        frequency = frame["frequency"]
        boundary = center - hop_half if previous_center is None else (previous_center + center) / 2

        if midi is None or frequency is None:
            _flush_note(notes, current_start, boundary, current_midis, current_freqs)
            current_start = None
            current_midis = []
            current_freqs = []
            previous_center = center
            continue

        if current_start is None:
            current_start = max(0.0, boundary)
            current_midis = [int(midi)]
            current_freqs = [float(frequency)]
            previous_center = center
            continue

        stable_reference = round(median(current_midis))
        if abs(int(midi) - stable_reference) <= PITCH_STABILITY_SEMITONES:
            current_midis.append(int(midi))
            current_freqs.append(float(frequency))
            previous_center = center
            continue

        _flush_note(notes, current_start, boundary, current_midis, current_freqs)
        current_start = boundary
        current_midis = [int(midi)]
        current_freqs = [float(frequency)]
        previous_center = center

    if frames:
        last_boundary = float(frames[-1]["center"]) + hop_half
        _flush_note(notes, current_start, last_boundary, current_midis, current_freqs)

    return notes


def _smooth_frames(frames: list[dict[str, float | int | None]]) -> None:
    if not frames:
        return

    original = [frame.copy() for frame in frames]
    for index, frame in enumerate(frames):
        if frame["midi"] is None:
            continue

        window_midis = [
            int(item["midi"])
            for item in original[max(0, index - SMOOTHING_WINDOW):index + SMOOTHING_WINDOW + 1]
            if item["midi"] is not None
        ]
        if not window_midis:
            continue

        smoothed_midi = round(median(window_midis))
        if abs(smoothed_midi - int(frame["midi"])) <= 2:
            frame["midi"] = smoothed_midi

        supporting_frequencies = [
            float(item["frequency"])
            for item in original[max(0, index - SMOOTHING_WINDOW):index + SMOOTHING_WINDOW + 1]
            if item["midi"] is not None and abs(int(item["midi"]) - smoothed_midi) <= 1 and item["frequency"] is not None
        ]
        if supporting_frequencies:
            frame["frequency"] = sum(supporting_frequencies) / len(supporting_frequencies)


def _suppress_short_frame_runs(frames: list[dict[str, float | int | None]]) -> None:
    if not frames:
        return

    index = 0
    while index < len(frames):
        midi = frames[index]["midi"]
        run_end = index + 1
        while run_end < len(frames) and frames[run_end]["midi"] == midi:
            run_end += 1

        run_length = run_end - index
        if midi is not None and run_length < MIN_SEGMENT_FRAMES:
            previous_midi = frames[index - 1]["midi"] if index > 0 else None
            next_midi = frames[run_end]["midi"] if run_end < len(frames) else None

            replacement = None
            if previous_midi is not None and previous_midi == next_midi:
                replacement = previous_midi
            elif previous_midi is not None and next_midi is not None:
                if abs(int(midi) - int(previous_midi)) <= MERGEABLE_PITCH_DISTANCE:
                    replacement = previous_midi
                elif abs(int(midi) - int(next_midi)) <= MERGEABLE_PITCH_DISTANCE:
                    replacement = next_midi

            for run_index in range(index, run_end):
                if replacement is None:
                    frames[run_index]["midi"] = None
                    frames[run_index]["frequency"] = None
                else:
                    frames[run_index]["midi"] = replacement

        index = run_end


def _flush_note(
    notes: list[DetectedNote],
    start_time: float | None,
    end_time: float,
    midis: list[int],
    frequencies: list[float],
) -> None:
    if start_time is None or not midis or not frequencies:
        return
    if end_time - start_time < MIN_NOTE_SECONDS:
        return

    midi = round(median(midis))
    frequency = sum(frequencies) / len(frequencies)
    notes.append(
        DetectedNote(
            midi=midi,
            start_time=start_time,
            end_time=end_time,
            frequency=frequency,
        )
    )
