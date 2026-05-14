from __future__ import annotations

import math
import xml.etree.ElementTree as xml
from pathlib import Path

from tabit_worker.fretboard import STANDARD_TUNING
from tabit_worker.models import QuantizedNote


DIVISIONS = 16
BEAT_TYPE = 4
BEATS_PER_MEASURE = 4
MEASURE_DURATION = BEATS_PER_MEASURE * DIVISIONS
STEP_NAMES = ["C", "C", "D", "D", "E", "F", "F", "G", "G", "A", "A", "B"]
ALTERS = [0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0]
TYPE_BY_DURATION = {
    64: ("whole", False),
    48: ("half", True),
    32: ("half", False),
    24: ("quarter", True),
    16: ("quarter", False),
    12: ("eighth", True),
    8: ("eighth", False),
    6: ("16th", True),
    4: ("16th", False),
    2: ("32nd", False),
    1: ("64th", False),
}


def write_musicxml(notes: list[QuantizedNote], output_path: str | Path, bpm: float) -> None:
    root = xml.Element("score-partwise", version="3.1")
    part_list = xml.SubElement(root, "part-list")
    score_part = xml.SubElement(part_list, "score-part", id="P1")
    xml.SubElement(score_part, "part-name").text = "Guitar"

    part = xml.SubElement(root, "part", id="P1")
    measure_events = _build_measure_events(notes)
    if not measure_events:
        measure_events = [[]]

    for index, events in enumerate(measure_events, start=1):
        measure = xml.SubElement(part, "measure", number=str(index))
        if index == 1:
            _add_attributes(measure)
            _add_tempo(measure, bpm)
        _write_measure(measure, events)

    xml.indent(root)
    text = xml.tostring(root, encoding="unicode")
    Path(output_path).write_text("<?xml version='1.0' encoding='utf-8'?>\n" + text, encoding="utf-8")


def _build_measure_events(notes: list[QuantizedNote]) -> list[list[dict[str, object]]]:
    measures: list[list[dict[str, object]]] = []
    for note in notes:
        remaining_duration = max(1, int(round(note.beats * DIVISIONS)))
        cursor_division = int(round(note.start_beat * DIVISIONS))

        while remaining_duration > 0:
            measure_index = cursor_division // MEASURE_DURATION
            offset_in_measure = cursor_division % MEASURE_DURATION
            while len(measures) <= measure_index:
                measures.append([])

            current_measure = measures[measure_index]
            filled = sum(int(event["duration"]) for event in current_measure)
            if filled < offset_in_measure:
                current_measure.append({"kind": "rest", "duration": offset_in_measure - filled})
                filled = offset_in_measure

            chunk = min(MEASURE_DURATION - filled, remaining_duration)
            current_measure.append({"kind": "note", "duration": chunk, "note": note})
            remaining_duration -= chunk
            cursor_division += chunk

    for measure in measures:
        filled = sum(int(event["duration"]) for event in measure)
        if filled < MEASURE_DURATION:
            measure.append({"kind": "rest", "duration": MEASURE_DURATION - filled})

    return measures


def _add_attributes(measure: xml.Element) -> None:
    attributes = xml.SubElement(measure, "attributes")
    xml.SubElement(attributes, "divisions").text = str(DIVISIONS)

    key = xml.SubElement(attributes, "key")
    xml.SubElement(key, "fifths").text = "0"

    time = xml.SubElement(attributes, "time")
    xml.SubElement(time, "beats").text = str(BEATS_PER_MEASURE)
    xml.SubElement(time, "beat-type").text = str(BEAT_TYPE)

    clef = xml.SubElement(attributes, "clef")
    xml.SubElement(clef, "sign").text = "TAB"
    xml.SubElement(clef, "line").text = "5"

    staff_details = xml.SubElement(attributes, "staff-details")
    xml.SubElement(staff_details, "staff-lines").text = "6"
    for line, (_string_number, step_name, midi) in enumerate(reversed(STANDARD_TUNING), start=1):
        tuning = xml.SubElement(staff_details, "staff-tuning", line=str(line))
        xml.SubElement(tuning, "tuning-step").text = step_name[0]
        octave = math.floor(midi / 12) - 1
        xml.SubElement(tuning, "tuning-octave").text = str(octave)


def _add_tempo(measure: xml.Element, bpm: float) -> None:
    direction = xml.SubElement(measure, "direction", placement="above")
    direction_type = xml.SubElement(direction, "direction-type")
    metronome = xml.SubElement(direction_type, "metronome")
    xml.SubElement(metronome, "beat-unit").text = "quarter"
    integer_bpm = max(1, int(round(bpm)))
    xml.SubElement(metronome, "per-minute").text = str(integer_bpm)
    sound = xml.SubElement(direction, "sound")
    sound.set("tempo", str(integer_bpm))


def _write_measure(measure: xml.Element, events: list[dict[str, object]]) -> None:
    for event in events:
        if event["kind"] == "rest":
            _add_rest(measure, int(event["duration"]))
        else:
            _add_note(measure, event["note"], int(event["duration"]))


def _add_rest(measure: xml.Element, duration: int) -> None:
    note = xml.SubElement(measure, "note")
    xml.SubElement(note, "rest")
    xml.SubElement(note, "duration").text = str(duration)
    note_type, dotted = _duration_to_type(duration)
    xml.SubElement(note, "type").text = note_type
    if dotted:
        xml.SubElement(note, "dot")


def _add_note(measure: xml.Element, quantized: QuantizedNote, duration: int) -> None:
    note = xml.SubElement(measure, "note")

    pitch = xml.SubElement(note, "pitch")
    pitch_class = quantized.midi % 12
    step = STEP_NAMES[pitch_class]
    alter = ALTERS[pitch_class]
    octave = quantized.midi // 12 - 1

    xml.SubElement(pitch, "step").text = step
    if alter:
        xml.SubElement(pitch, "alter").text = str(alter)
    xml.SubElement(pitch, "octave").text = str(octave)

    xml.SubElement(note, "duration").text = str(duration)
    note_type, dotted = _duration_to_type(duration)
    xml.SubElement(note, "type").text = note_type
    if dotted:
        xml.SubElement(note, "dot")

    notations = xml.SubElement(note, "notations")
    technical = xml.SubElement(notations, "technical")
    xml.SubElement(technical, "string").text = str(quantized.string)
    xml.SubElement(technical, "fret").text = str(quantized.fret)


def _duration_to_type(duration: int) -> tuple[str, bool]:
    if duration in TYPE_BY_DURATION:
        return TYPE_BY_DURATION[duration]

    base = min(TYPE_BY_DURATION, key=lambda candidate: abs(candidate - duration))
    return TYPE_BY_DURATION[base]
