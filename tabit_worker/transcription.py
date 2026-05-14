from __future__ import annotations

import tempfile
from pathlib import Path

import numpy

from tabit_worker.audio import decode_with_ffmpeg, load_audio_mono, resolve_ffmpeg_path
from tabit_worker.models import DetectedNote
from tabit_worker.pitch import cleanup_notes


MINIMUM_NOTE_LENGTH_MS = 120.0
ONSET_THRESHOLD = 0.45
FRAME_THRESHOLD = 0.25
MINIMUM_FREQUENCY = 82.41
MAXIMUM_FREQUENCY = 987.77
PYIN_FRAME_LENGTH = 2048
PYIN_HOP_LENGTH = 256
PYIN_PITCH_TOLERANCE = 0.75
ONSET_SPLIT_MIN_SECONDS = 0.12
ONSET_EDGE_GUARD_SECONDS = 0.01
MIN_ONSET_SEPARATION_SECONDS = 0.06
ONSET_STRENGTH_THRESHOLD = 0.08
RMS_REATTACK_RATIO = 0.18


def transcribe_audio(
    path: str | Path,
    bpm: float,
    ffmpeg_path: str | Path | None = None,
    backend: str = "pyin",
) -> list[DetectedNote]:
    normalized_backend = backend.lower()
    if normalized_backend == "pyin":
        return _transcribe_with_pyin(path, ffmpeg_path)
    if normalized_backend == "basic-pitch":
        return _transcribe_with_basic_pitch(path, bpm, ffmpeg_path)
    raise ValueError("Supported backends are 'pyin' and 'basic-pitch'.")


def _transcribe_with_pyin(
    path: str | Path,
    ffmpeg_path: str | Path | None,
) -> list[DetectedNote]:
    import librosa

    samples, sample_rate = load_audio_mono(path, ffmpeg_path=ffmpeg_path)
    waveform = numpy.asarray(samples, dtype=numpy.float32)
    f0, voiced_flag, _ = librosa.pyin(
        waveform,
        sr=sample_rate,
        fmin=MINIMUM_FREQUENCY,
        fmax=MAXIMUM_FREQUENCY,
        frame_length=PYIN_FRAME_LENGTH,
        hop_length=PYIN_HOP_LENGTH,
    )

    if f0 is None or voiced_flag is None:
        return []

    times = librosa.times_like(f0, sr=sample_rate, hop_length=PYIN_HOP_LENGTH)
    onset_times = _detect_onset_times(waveform, sample_rate)
    midi_values = [float(librosa.hz_to_midi(value)) if value and not numpy.isnan(value) else None for value in f0]
    smoothed = _smooth_midi_track(midi_values, voiced_flag)
    notes = _segment_pyin_frames(smoothed, times, onset_times)
    return cleanup_notes(notes)


def _transcribe_with_basic_pitch(
    path: str | Path,
    bpm: float,
    ffmpeg_path: str | Path | None = None,
) -> list[DetectedNote]:
    source = Path(path)
    prepared_path, temp_dir = _prepare_audio_path(source, ffmpeg_path)
    try:
        from basic_pitch.inference import predict

        _, _, note_events = predict(
            prepared_path,
            onset_threshold=ONSET_THRESHOLD,
            frame_threshold=FRAME_THRESHOLD,
            minimum_note_length=MINIMUM_NOTE_LENGTH_MS,
            minimum_frequency=MINIMUM_FREQUENCY,
            maximum_frequency=MAXIMUM_FREQUENCY,
            midi_tempo=bpm,
        )
        notes = [
            DetectedNote(
                midi=int(midi),
                start_time=float(start_time),
                end_time=float(end_time),
                frequency=_midi_to_frequency(int(midi)),
            )
            for start_time, end_time, midi, _amplitude, _pitch_bend in note_events
        ]
        return _cleanup_detected_notes(notes)
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


def _prepare_audio_path(
    source: Path,
    ffmpeg_path: str | Path | None,
) -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
    if source.suffix.lower() != ".mp3":
        return source, None

    temp_dir = tempfile.TemporaryDirectory()
    temp_path = Path(temp_dir.name) / (source.stem + ".wav")
    ffmpeg_binary = resolve_ffmpeg_path(ffmpeg_path)
    wav_bytes = decode_with_ffmpeg(source, ffmpeg_binary)
    temp_path.write_bytes(wav_bytes)
    return temp_path, temp_dir


def _detect_onset_times(waveform: numpy.ndarray, sample_rate: int) -> list[float]:
    import librosa

    onset_envelope = librosa.onset.onset_strength(
        y=waveform,
        sr=sample_rate,
        hop_length=PYIN_HOP_LENGTH,
        lag=1,
        max_size=1,
    )
    spectral_peaks = librosa.util.peak_pick(
        onset_envelope,
        pre_max=1,
        post_max=1,
        pre_avg=2,
        post_avg=2,
        delta=ONSET_STRENGTH_THRESHOLD,
        wait=1,
    )

    rms = librosa.feature.rms(y=waveform, frame_length=PYIN_FRAME_LENGTH, hop_length=PYIN_HOP_LENGTH)[0]
    rms_delta = numpy.diff(rms, prepend=rms[0])
    rms_threshold = max(RMS_REATTACK_RATIO * float(numpy.max(rms) or 0.0), 0.01)
    rms_peaks = librosa.util.peak_pick(
        rms_delta,
        pre_max=1,
        post_max=1,
        pre_avg=2,
        post_avg=2,
        delta=rms_threshold,
        wait=1,
    )

    onset_times = librosa.frames_to_time(spectral_peaks, sr=sample_rate, hop_length=PYIN_HOP_LENGTH)
    rms_times = librosa.frames_to_time(rms_peaks, sr=sample_rate, hop_length=PYIN_HOP_LENGTH)
    return _merge_onset_times(
        [float(value) for value in onset_times] + [float(value) for value in rms_times]
    )


def _merge_onset_times(candidates: list[float]) -> list[float]:
    if not candidates:
        return []

    ordered = sorted(candidate for candidate in candidates if candidate >= 0.0)
    merged = [ordered[0]]
    for candidate in ordered[1:]:
        if candidate - merged[-1] < MIN_ONSET_SEPARATION_SECONDS:
            merged[-1] = (merged[-1] + candidate) / 2
            continue
        merged.append(candidate)
    return merged


def _smooth_midi_track(midi_values: list[float | None], voiced_flag: numpy.ndarray) -> list[float | None]:
    smoothed: list[float | None] = []
    window = 2
    for index, midi_value in enumerate(midi_values):
        if midi_value is None or not bool(voiced_flag[index]):
            smoothed.append(None)
            continue

        neighbors = [
            value
            for value in midi_values[max(0, index - window):index + window + 1]
            if value is not None
        ]
        if not neighbors:
            smoothed.append(midi_value)
            continue
        smoothed.append(float(numpy.median(numpy.asarray(neighbors, dtype=numpy.float32))))
    return smoothed


def _segment_pyin_frames(
    midi_values: list[float | None],
    times: numpy.ndarray,
    onset_times: list[float] | None = None,
) -> list[DetectedNote]:
    notes: list[DetectedNote] = []
    if len(times) == 0:
        return notes

    hop_seconds = float(times[1] - times[0]) if len(times) > 1 else PYIN_HOP_LENGTH / 22050.0
    current_start = None
    current_midis: list[float] = []
    pending_onsets = list(onset_times or [])
    onset_index = 0

    for index, midi_value in enumerate(midi_values):
        frame_time = float(times[index])
        boundary = max(0.0, frame_time - hop_seconds / 2)

        if midi_value is None:
            _flush_segment(notes, current_start, frame_time, current_midis)
            current_start = None
            current_midis = []
            continue

        if current_start is None:
            current_start = boundary
            current_midis = [midi_value]
            continue

        while onset_index < len(pending_onsets) and pending_onsets[onset_index] <= current_start + ONSET_EDGE_GUARD_SECONDS:
            onset_index += 1

        split_time = _pick_split_time(current_start, boundary, pending_onsets, onset_index)
        if split_time is not None:
            _flush_segment(notes, current_start, split_time, current_midis)
            current_start = split_time
            current_midis = []
            while onset_index < len(pending_onsets) and pending_onsets[onset_index] <= split_time + ONSET_EDGE_GUARD_SECONDS:
                onset_index += 1

        reference = float(numpy.median(numpy.asarray(current_midis, dtype=numpy.float32))) if current_midis else midi_value
        if abs(midi_value - reference) <= PYIN_PITCH_TOLERANCE:
            current_midis.append(midi_value)
            continue

        _flush_segment(notes, current_start, frame_time, current_midis)
        current_start = boundary
        current_midis = [midi_value]

    if current_midis:
        _flush_segment(notes, current_start, float(times[-1]) + hop_seconds / 2, current_midis)

    return notes


def _pick_split_time(
    current_start: float,
    boundary: float,
    onset_times: list[float],
    onset_index: int,
) -> float | None:
    if onset_index >= len(onset_times):
        return None

    candidate = onset_times[onset_index]
    if candidate <= current_start + ONSET_SPLIT_MIN_SECONDS:
        return None
    if candidate >= boundary - ONSET_EDGE_GUARD_SECONDS:
        return None
    return candidate


def _flush_segment(
    notes: list[DetectedNote],
    start_time: float | None,
    end_time: float,
    midis: list[float],
) -> None:
    if start_time is None or not midis:
        return
    if end_time <= start_time:
        return

    midi = int(round(float(numpy.median(numpy.asarray(midis, dtype=numpy.float32)))))
    notes.append(
        DetectedNote(
            midi=midi,
            start_time=start_time,
            end_time=end_time,
            frequency=_midi_to_frequency(midi),
        )
    )


def _cleanup_detected_notes(notes: list[DetectedNote]) -> list[DetectedNote]:
    if not notes:
        return []

    ordered = sorted(notes, key=lambda item: (item.start_time, item.end_time, item.midi))
    cleaned: list[DetectedNote] = []
    for note in ordered:
        if note.end_time <= note.start_time:
            continue

        if cleaned and note.start_time <= cleaned[-1].end_time and abs(note.midi - cleaned[-1].midi) <= 1:
            previous = cleaned[-1]
            cleaned[-1] = DetectedNote(
                midi=previous.midi,
                start_time=previous.start_time,
                end_time=max(previous.end_time, note.end_time),
                frequency=(previous.frequency + note.frequency) / 2,
            )
            continue

        cleaned.append(note)

    return cleanup_notes(cleaned)


def _midi_to_frequency(midi: int) -> float:
    return 440.0 * (2 ** ((midi - 69) / 12))
