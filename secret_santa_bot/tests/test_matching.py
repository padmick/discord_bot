import asyncio
import random

import pytest

from matching import assign_derangement, is_derangement, reroll_after_removal, reroll_for_exclusions


def test_two_people_swap():
    assert assign_derangement(["a", "b"], set(), random.Random(1)) == {"a": "b", "b": "a"}


def test_three_people_have_no_fixed_points():
    for seed in range(30):
        result = assign_derangement(["a", "b", "c"], set(), random.Random(seed))
        assert is_derangement(result, ["a", "b", "c"])


def test_shuffle_is_seeded():
    people = ["a", "b", "c", "d"]
    first = assign_derangement(people, set(), random.Random(0))
    second = assign_derangement(people, set(), random.Random(0))
    assert first == second
    assert is_derangement(first, people)


def test_impossible_exclusions_return_none():
    blocked = {("a", "b"), ("b", "a")}
    assert assign_derangement(["a", "b", "c"], blocked, random.Random(0)) is None


def test_forced_unique_cycle():
    exclusions = {("a", "c"), ("b", "a"), ("c", "b")}
    assert assign_derangement(["a", "b", "c"], exclusions, random.Random(4)) == {
        "a": "b",
        "b": "c",
        "c": "a",
    }


def test_mutual_exclusion_is_respected():
    exclusions = {("a", "b"), ("b", "a")}
    result = assign_derangement(["a", "b", "c", "d"], exclusions, random.Random(2))
    assert result is not None
    assert result["a"] != "b"
    assert result["b"] != "a"
    assert is_derangement(result, ["a", "b", "c", "d"], exclusions)


def test_fewer_than_two_people_cannot_be_drawn():
    assert assign_derangement(["only"], set(), random.Random(0)) is None
    assert assign_derangement([], set(), random.Random(0)) is None


def test_reroll_stitches_a_three_cycle_without_changing_anyone_else():
    result = reroll_after_removal(
        {"a": "b", "b": "c", "c": "a"},
        "b",
        set(),
        random.Random(0),
    )
    assert result == {"a": "c", "c": "a"}


def test_reroll_of_a_two_cycle_changes_another_pair():
    result = reroll_after_removal(
        {"a": "b", "b": "a", "c": "d", "d": "c"},
        "b",
        set(),
        random.Random(0),
    )
    assert is_derangement(result, ["a", "c", "d"])
    assert not (result["c"] == "d" and result["d"] == "c")


def test_reroll_returns_none_below_two_people():
    assert reroll_after_removal({"a": "b", "b": "a"}, "b", set(), random.Random(0)) is None


def test_reroll_respects_exclusions():
    blocked = {("a", "c")}
    assert reroll_after_removal(
        {"a": "b", "b": "c", "c": "a"},
        "b",
        blocked,
        random.Random(0),
    ) is None

    result = reroll_after_removal(
        {"a": "b", "b": "c", "c": "d", "d": "a"},
        "b",
        blocked,
        random.Random(1),
    )
    assert result is not None
    assert result["a"] != "c"
    assert is_derangement(result, ["a", "c", "d"], blocked)


def test_reroll_for_a_new_block_keeps_legal_edges():
    current = {"a": "b", "b": "c", "c": "d", "d": "a"}
    blocked = {("a", "b"), ("b", "a")}
    same = reroll_for_exclusions({"a": "b", "b": "a"}, set(), random.Random(0))
    assert same == {"a": "b", "b": "a"}
    result = reroll_for_exclusions(current, blocked, random.Random(0))
    assert result is not None
    assert result["a"] != "b" and result["b"] != "a"
    assert is_derangement(result, list(current), blocked)
    kept = sum(1 for giver, receiver in current.items() if result[giver] == receiver)
    assert kept >= 2
    impossible = {"a": "b", "b": "c", "c": "a"}
    assert reroll_for_exclusions(impossible, blocked, random.Random(0)) is None


def test_reroll_of_unknown_person_returns_none():
    assert reroll_after_removal({"a": "b", "b": "a"}, "z", set(), random.Random(0)) is None


@pytest.mark.asyncio
async def test_derangement_runs_in_a_worker_thread():
    result = await asyncio.to_thread(
        assign_derangement,
        ["a", "b"],
        set(),
        random.Random(0),
    )
    assert result == {"a": "b", "b": "a"}
