from __future__ import annotations

from pathlib import Path

import numpy

from tabit_worker.audio import load_audio_mono


DEFAULT_FALLBACK_BPM = 120.0
MIN_BPM = 40.0
MAX_BPM = 220.0


def estimate_bpm(path: str | Path, ffmpeg_path: str | Path | None = None) -> float:
    import librosa

    samples, sample_rate = load_audio_mono(path, ffmpeg_path=ffmpeg_path)
    waveform = numpy.asarray(samples, dtype=numpy.float32)
    if waveform.size == 0:
        return DEFAULT_FALLBACK_BPM

    onset_envelope = numpy.asarray(
        librosa.onset.onset_strength(y=waveform, sr=sample_rate),
        dtype=numpy.float32,
    )
    if onset_envelope.size == 0 or float(numpy.max(onset_envelope)) <= 0:
        return DEFAULT_FALLBACK_BPM

    tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset_envelope,
        sr=sample_rate,
        trim=False,
        start_bpm=DEFAULT_FALLBACK_BPM,
        units="frames",
    )
    bpm = _coerce_bpm(tempo)
    beat_frames_array = numpy.asarray(beat_frames) if beat_frames is not None else numpy.asarray([])

    if beat_frames_array.size >= 2:
        beat_times = numpy.asarray(librosa.frames_to_time(beat_frames_array, sr=sample_rate), dtype=numpy.float32)
        intervals = numpy.diff(beat_times)
        valid_intervals = intervals[intervals > 0]
        if valid_intervals.size > 0:
            median_bpm = 60.0 / float(numpy.median(valid_intervals))
            if MIN_BPM <= median_bpm <= MAX_BPM:
                bpm = median_bpm

    return _normalize_bpm(bpm)


def _coerce_bpm(value: object) -> float:
    if isinstance(value, numpy.ndarray):
        if value.size == 0:
            return DEFAULT_FALLBACK_BPM
        return float(value.reshape(-1)[0])
    if value is None:
        return DEFAULT_FALLBACK_BPM
    return float(value)


def _normalize_bpm(bpm: float) -> float:
    if bpm <= 0 or numpy.isnan(bpm):
        return DEFAULT_FALLBACK_BPM
    while bpm < MIN_BPM:
        bpm *= 2.0
    while bpm > MAX_BPM:
        bpm /= 2.0
    return round(bpm, 2)
