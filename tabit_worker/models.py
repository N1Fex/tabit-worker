from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DetectedNote:
    midi: int
    start_time: float
    end_time: float
    frequency: float

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time


@dataclass(frozen=True)
class QuantizedNote:
    midi: int
    start_beat: float
    beats: float
    frequency: float
    string: int
    fret: int


@dataclass(frozen=True)
class GuitarPosition:
    string: int
    fret: int
