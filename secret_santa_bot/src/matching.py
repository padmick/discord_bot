"""Secret Santa derangements.

The draw is a permutation with no fixed points and no excluded edges.
A reroll after someone drops keeps every assignment that did not involve
that person when that is possible, and otherwise changes as few as it can.
"""

from __future__ import annotations

import random


def is_derangement(
    assignment: dict[str, str],
    people: list[str],
    exclusions: set[tuple[str, str]] | None = None,
) -> bool:
    """Return True when assignment is a complete derangement of people."""
    blocked = exclusions or set()
    population = set(people)
    if set(assignment) != population or set(assignment.values()) != population:
        return False
    for giver, receiver in assignment.items():
        if giver == receiver or (giver, receiver) in blocked:
            return False
    return True


def assign_derangement(
    user_ids: list[str],
    exclusions: set[tuple[str, str]],
    rng: random.Random,
) -> dict[str, str] | None:
    """Return one valid derangement, or None when every draw is blocked.

    The people and the receiver search order are shuffled once. The search
    itself does not reshuffle and does not call itself recursively.
    """
    people = list(dict.fromkeys(user_ids))
    if len(people) < 2:
        return None

    order = people[:]
    rng.shuffle(order)
    receivers = people[:]
    rng.shuffle(receivers)

    assignment: dict[str, str] = {}
    used: set[str] = set()
    cursors = [0] * len(order)
    index = 0
    while True:
        if index == len(order):
            return dict(assignment)
        giver = order[index]
        placed = False
        while cursors[index] < len(receivers):
            receiver = receivers[cursors[index]]
            cursors[index] += 1
            if receiver in used or receiver == giver or (giver, receiver) in exclusions:
                continue
            used.add(receiver)
            assignment[giver] = receiver
            index += 1
            placed = True
            break
        if placed:
            continue
        if index == 0:
            return None
        cursors[index] = 0
        index -= 1
        used.remove(assignment.pop(order[index]))


def reroll_after_removal(
    pairings: dict[str, str],
    removed_id: str,
    exclusions: set[tuple[str, str]],
    rng: random.Random,
) -> dict[str, str] | None:
    """Redraw after removed_id leaves, preserving as many edges as possible.

    When the giver of the removed person can take that person's recipient,
    every other edge stays. Otherwise the search picks a derangement of the
    remaining people with the largest number of preserved edges.
    """
    if removed_id not in pairings:
        return None

    remaining = [user_id for user_id in pairings if user_id != removed_id]
    if len(remaining) < 2:
        return None

    giver_of_removed = next(giver for giver, receiver in pairings.items() if receiver == removed_id)
    receiver_of_removed = pairings[removed_id]
    preserved = {
        giver: receiver
        for giver, receiver in pairings.items()
        if giver != removed_id and receiver != removed_id
    }

    def allowed(giver: str, receiver: str) -> bool:
        return giver != receiver and (giver, receiver) not in exclusions

    if (
        giver_of_removed in remaining
        and receiver_of_removed in remaining
        and allowed(giver_of_removed, receiver_of_removed)
    ):
        stitched = dict(preserved)
        stitched[giver_of_removed] = receiver_of_removed
        if is_derangement(stitched, remaining, exclusions):
            return stitched

    return _best_derangement(remaining, preserved, exclusions, rng)


def _best_derangement(
    people: list[str],
    preserved: dict[str, str],
    exclusions: set[tuple[str, str]],
    rng: random.Random,
) -> dict[str, str] | None:
    order = people[:]
    rng.shuffle(order)
    receiver_order = people[:]
    rng.shuffle(receiver_order)

    def allowed(giver: str, receiver: str) -> bool:
        return giver != receiver and (giver, receiver) not in exclusions

    upper = sum(
        1
        for giver, receiver in preserved.items()
        if giver in people and receiver in people and allowed(giver, receiver)
    )

    best: dict[str, str] | None = None
    best_score = -1
    assignment: dict[str, str] = {}
    used: set[str] = set()

    def search(index: int, score: int) -> bool:
        nonlocal best, best_score
        if best_score == upper and best is not None:
            return True

        if index == len(order):
            if score > best_score:
                best_score = score
                best = dict(assignment)
            return best_score == upper

        potential = score
        for giver in order[index:]:
            preferred = preserved.get(giver)
            if preferred is not None and preferred not in used and allowed(giver, preferred):
                potential += 1
        if potential <= best_score:
            return False

        giver = order[index]
        candidates: list[str] = []
        preferred = preserved.get(giver)
        if preferred is not None:
            candidates.append(preferred)
        candidates.extend(receiver for receiver in receiver_order if receiver != preferred)

        for receiver in candidates:
            if receiver in used or not allowed(giver, receiver):
                continue
            used.add(receiver)
            assignment[giver] = receiver
            gained = 1 if preserved.get(giver) == receiver else 0
            if search(index + 1, score + gained):
                return True
            del assignment[giver]
            used.remove(receiver)
        return False

    search(0, 0)
    return best


def reroll_for_exclusions(
    pairings: dict[str, str],
    exclusions: set[tuple[str, str]],
    rng: random.Random,
) -> dict[str, str] | None:
    """Redraw so the current pairs respect exclusions, keeping every legal edge possible."""
    people = list(pairings)
    if len(people) < 2:
        return None
    if is_derangement(pairings, people, exclusions):
        return dict(pairings)
    preserved = {
        giver: receiver
        for giver, receiver in pairings.items()
        if giver != receiver and (giver, receiver) not in exclusions
    }
    return _best_derangement(people, preserved, exclusions, rng)
