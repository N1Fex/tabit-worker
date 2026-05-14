from __future__ import annotations

from tabit_worker.models import GuitarPosition


STANDARD_TUNING = [
    (1, "E4", 64),
    (2, "B3", 59),
    (3, "G3", 55),
    (4, "D3", 50),
    (5, "A2", 45),
    (6, "E2", 40),
]

MAX_FRET = 20
TARGET_FRET = 5
OPEN_STRING_PENALTY = 1.5
LOW_POSITION_WEIGHT = 0.35
FRET_JUMP_WEIGHT = 1.8
STRING_JUMP_WEIGHT = 1.8
LARGE_SHIFT_THRESHOLD = 4
LARGE_SHIFT_PENALTY = 4.0


def choose_position(midi: int, previous: GuitarPosition | None = None) -> GuitarPosition:
    candidates = get_candidate_positions(midi)
    if previous is None:
        return min(candidates, key=_initial_cost)
    return min(candidates, key=lambda item: _transition_cost(previous, item) + _initial_cost(item))


def choose_positions(midis: list[int]) -> list[GuitarPosition]:
    if not midis:
        return []

    candidate_rows = [get_candidate_positions(midi) for midi in midis]
    costs: list[list[float]] = []
    backpointers: list[list[int | None]] = []

    first_costs = [_initial_cost(candidate) for candidate in candidate_rows[0]]
    costs.append(first_costs)
    backpointers.append([None] * len(candidate_rows[0]))

    for row_index in range(1, len(candidate_rows)):
        row_costs: list[float] = []
        row_backpointers: list[int | None] = []
        for candidate in candidate_rows[row_index]:
            best_cost = float("inf")
            best_previous_index: int | None = None
            for previous_index, previous_candidate in enumerate(candidate_rows[row_index - 1]):
                transition = _transition_cost(previous_candidate, candidate)
                total_cost = costs[row_index - 1][previous_index] + transition + _initial_cost(candidate)
                if total_cost < best_cost:
                    best_cost = total_cost
                    best_previous_index = previous_index
            row_costs.append(best_cost)
            row_backpointers.append(best_previous_index)
        costs.append(row_costs)
        backpointers.append(row_backpointers)

    last_index = min(range(len(costs[-1])), key=lambda index: costs[-1][index])
    resolved: list[GuitarPosition] = []
    for row_index in range(len(candidate_rows) - 1, -1, -1):
        resolved.append(candidate_rows[row_index][last_index])
        previous_index = backpointers[row_index][last_index]
        if previous_index is None:
            break
        last_index = previous_index

    return list(reversed(resolved))


def get_candidate_positions(midi: int) -> list[GuitarPosition]:
    candidates: list[GuitarPosition] = []
    for string_number, _, open_midi in STANDARD_TUNING:
        fret = midi - open_midi
        if 0 <= fret <= MAX_FRET:
            candidates.append(GuitarPosition(string=string_number, fret=fret))

    if not candidates:
        raise ValueError(f"Note MIDI {midi} is outside standard guitar range.")

    return candidates


def _initial_cost(position: GuitarPosition) -> float:
    cost = abs(position.fret - TARGET_FRET) * LOW_POSITION_WEIGHT
    if position.fret == 0:
        cost += OPEN_STRING_PENALTY
    return cost


def _transition_cost(previous: GuitarPosition, current: GuitarPosition) -> float:
    fret_jump = abs(current.fret - previous.fret)
    string_jump = abs(current.string - previous.string)
    cost = fret_jump * FRET_JUMP_WEIGHT + string_jump * STRING_JUMP_WEIGHT
    if previous.string == current.string:
        cost -= 1.0
    if fret_jump > LARGE_SHIFT_THRESHOLD:
        cost += (fret_jump - LARGE_SHIFT_THRESHOLD) * LARGE_SHIFT_PENALTY
    if previous.string == current.string and current.fret < previous.fret:
        cost += 0.6
    return cost
