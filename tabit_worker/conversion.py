from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from tabit_worker.musicxml import write_musicxml
from tabit_worker.quantize import quantize_notes
from tabit_worker.tempo import estimate_bpm
from tabit_worker.transcription import transcribe_audio


DEFAULT_BACKEND = "pyin"
DEFAULT_BPM = "auto"


def add_common_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument(
        "--bpm",
        default=DEFAULT_BPM,
        help="Tempo for note duration quantization. Use a number or 'auto'. Default: auto.",
    )
    parser.add_argument(
        "--backend",
        choices=["pyin", "basic-pitch"],
        default=DEFAULT_BACKEND,
        help="Transcription engine. Default: pyin.",
    )
    parser.add_argument(
        "--ffmpeg",
        help="Optional path to ffmpeg executable for MP3 decoding fallback.",
    )
    return parser


def convert_audio_file(
    input_path: str | Path,
    output_path: str | Path,
    bpm_argument: str = DEFAULT_BPM,
    backend: str = DEFAULT_BACKEND,
    ffmpeg_path: str | Path | None = None,
) -> float:
    bpm = resolve_bpm_argument(bpm_argument, input_path, ffmpeg_path)
    detected = transcribe_audio(
        input_path,
        bpm=bpm,
        ffmpeg_path=ffmpeg_path,
        backend=backend,
    )
    if not detected:
        raise ValueError("No stable notes were detected. Use a cleaner audio file or adjust the source recording.")

    quantized = quantize_notes(detected, bpm=bpm)
    write_musicxml(quantized, output_path=Path(output_path), bpm=bpm)
    return bpm


def convert_audio_bytes(
    audio_bytes: bytes,
    suffix: str,
    bpm_argument: str = DEFAULT_BPM,
    backend: str = DEFAULT_BACKEND,
    ffmpeg_path: str | Path | None = None,
) -> tuple[bytes, float]:
    normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = Path(temp_dir) / f"input{normalized_suffix}"
        output_path = Path(temp_dir) / "result.musicxml"
        input_path.write_bytes(audio_bytes)
        bpm = convert_audio_file(
            input_path=input_path,
            output_path=output_path,
            bpm_argument=bpm_argument,
            backend=backend,
            ffmpeg_path=ffmpeg_path,
        )
        return output_path.read_bytes(), bpm


def resolve_bpm_argument(
    bpm_argument: str | float,
    input_path: str | Path,
    ffmpeg_path: str | Path | None = None,
) -> float:
    if str(bpm_argument).strip().lower() == "auto":
        return estimate_bpm(str(input_path), ffmpeg_path=str(ffmpeg_path) if ffmpeg_path else None)

    bpm = float(bpm_argument)
    if bpm <= 0:
        raise ValueError("BPM must be positive.")
    return bpm
