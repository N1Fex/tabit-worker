from __future__ import annotations

import argparse

from tabit_worker.conversion import add_common_arguments, convert_audio_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert a monophonic WAV or MP3 file into guitar tablature MusicXML."
    )
    parser.add_argument("input", help="Path to input WAV or MP3 file.")
    parser.add_argument("output", help="Path to output MusicXML file.")
    return add_common_arguments(parser)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        bpm = convert_audio_file(
            input_path=args.input,
            output_path=args.output,
            bpm_argument=args.bpm,
            backend=args.backend,
            ffmpeg_path=args.ffmpeg,
        )
    except ValueError as error:
        parser.error(str(error))
        raise error

    print(f"Using BPM: {bpm}")
    print(f"Saved MusicXML to {args.output}")
    return 0
