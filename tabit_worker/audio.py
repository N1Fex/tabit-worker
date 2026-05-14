from __future__ import annotations

import array
import io
import os
import shutil
import subprocess
import wave
from pathlib import Path


PROJECT_FFMPEG_PATTERNS = [
    Path("ffmpeg.exe"),
    Path("bin") / "ffmpeg.exe",
    Path("tools") / "ffmpeg" / "bin" / "ffmpeg.exe",
    Path("vendor") / "ffmpeg" / "bin" / "ffmpeg.exe",
    Path(".local") / "ffmpeg" / "bin" / "ffmpeg.exe",
]

SYSTEM_FFMPEG_DIRS = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
    Path(os.environ.get("ProgramFiles", "")),
    Path(os.environ.get("ProgramFiles(x86)", "")),
    Path("C:/tools"),
    Path("C:/ffmpeg"),
    Path("C:/ProgramData/chocolatey/bin"),
    Path.home() / "scoop" / "apps",
]


def load_audio_mono(path: str | Path, ffmpeg_path: str | Path | None = None) -> tuple[list[float], int]:
    source = Path(path)
    suffix = source.suffix.lower()

    if suffix == ".wav":
        return load_wav_mono(source)
    if suffix == ".mp3":
        ffmpeg_binary = resolve_ffmpeg_path(ffmpeg_path)
        wav_bytes = decode_with_ffmpeg(source, ffmpeg_binary)
        return load_wav_bytes(wav_bytes)

    raise ValueError("Supported input formats are WAV and MP3.")


def load_wav_mono(path: str | Path) -> tuple[list[float], int]:
    with wave.open(str(Path(path)), "rb") as wav_file:
        return _read_wav_stream(wav_file)


def load_wav_bytes(payload: bytes) -> tuple[list[float], int]:
    with wave.open(io.BytesIO(payload), "rb") as wav_file:
        return _read_wav_stream(wav_file)


def _read_wav_stream(wav_file: wave.Wave_read) -> tuple[list[float], int]:
    sample_rate = wav_file.getframerate()
    sample_width = wav_file.getsampwidth()
    channels = wav_file.getnchannels()
    frames = wav_file.readframes(wav_file.getnframes())

    if sample_width not in {2, 4}:
        raise ValueError("Supported WAV sample widths are 16-bit and 32-bit PCM.")

    type_code = "h" if sample_width == 2 else "i"
    raw_samples = array.array(type_code)
    raw_samples.frombytes(frames)

    if channels > 1:
        mono = []
        for index in range(0, len(raw_samples), channels):
            channel_values = raw_samples[index:index + channels]
            mono.append(sum(channel_values) / len(channel_values))
    else:
        mono = list(raw_samples)

    scale = float(2 ** (sample_width * 8 - 1))
    normalized = [sample / scale for sample in mono]
    return normalized, sample_rate


def resolve_ffmpeg_path(ffmpeg_path: str | Path | None = None) -> str:
    candidates: list[Path | str] = []
    if ffmpeg_path:
        candidates.append(Path(ffmpeg_path))

    env_path = os.environ.get("TABIT_FFMPEG")
    if env_path:
        candidates.append(Path(env_path))

    path_binary = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if path_binary:
        candidates.append(path_binary)

    for candidate in PROJECT_FFMPEG_PATTERNS:
        candidates.append(Path.cwd() / candidate)

    candidates.extend(_discover_system_ffmpeg_candidates())

    seen: set[str] = set()
    for candidate in candidates:
        candidate_path = Path(candidate)
        normalized = str(candidate_path).lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        if candidate_path.is_file():
            return str(candidate_path)

    raise RuntimeError(
        "MP3 decoding requires ffmpeg.exe. The program now looks for it automatically in PATH, "
        "common Windows install folders, and inside the project under tools/ffmpeg/bin/ffmpeg.exe. "
        "If it is still not found, place ffmpeg.exe into tools/ffmpeg/bin/."
    )


def _discover_system_ffmpeg_candidates() -> list[Path]:
    discovered: list[Path] = []
    for base_dir in SYSTEM_FFMPEG_DIRS:
        if not base_dir or not str(base_dir):
            continue
        if not base_dir.exists():
            continue
        if base_dir.is_file() and base_dir.name.lower() == "ffmpeg.exe":
            discovered.append(base_dir)
            continue

        try:
            discovered.extend(base_dir.rglob("ffmpeg.exe"))
        except OSError:
            continue
    return discovered


def decode_with_ffmpeg(source: Path, ffmpeg_binary: str) -> bytes:
    command = [
        ffmpeg_binary,
        "-v",
        "error",
        "-i",
        str(source),
        "-f",
        "wav",
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",
        "pipe:1",
    ]
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip() or "Unknown ffmpeg error."
        raise RuntimeError(f"ffmpeg failed to decode MP3: {message}")
    return result.stdout
