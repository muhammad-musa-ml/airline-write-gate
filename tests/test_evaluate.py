import json
from pathlib import Path

import pytest
from builders import call, reservation, result

from airline_write_gate.evaluate import (
    evaluate_results,
    in_gold,
    markdown_table,
    matches,
    removed_actions,
    to_plain,
)
from airline_write_gate.gate import load_policy

FIXTURES = Path(__file__).parent / "fixtures"
POLICY = load_policy()


def fixture():
    data = json.loads((FIXTURES / "runs.json").read_text(encoding="utf-8"))
    later = {t["id"]: t for t in json.loads((FIXTURES / "later_tasks.json").read_text(encoding="utf-8"))}
    return data, later


def task(actions):
    return {"id": "1", "evaluation_criteria": {"actions": [{"name": n, "arguments": a} for n, a in actions]}}


def test_a_gold_action_matches_when_every_argument_it_names_is_equal():
    action = {"name": "cancel_reservation", "arguments": {"reservation_id": "R1"}}
    assert matches(action, "cancel_reservation", {"reservation_id": "R1", "extra": 1})
    assert not matches(action, "cancel_reservation", {"reservation_id": "R2"})
    assert not matches(action, "book_reservation", {"reservation_id": "R1"})


def test_a_gold_action_that_names_two_arguments_needs_both():
    action = {"name": "update_reservation_baggages", "arguments": {"reservation_id": "R1", "total_baggages": 2}}
    assert matches(action, "update_reservation_baggages", {"reservation_id": "R1", "total_baggages": 2})
    assert not matches(action, "update_reservation_baggages", {"reservation_id": "R1", "total_baggages": 3})
    assert not matches(action, "update_reservation_baggages", {"reservation_id": "R2", "total_baggages": 2})


def synthetic(messages, reward=1.0):
    """One recorded run of a task whose gold is a cancellation of R1, in the shape of a results file."""
    return {
        "info": {"agent_info": {"llm": "m"}},
        "tasks": [task([("cancel_reservation", {"reservation_id": "R1"})])],
        "simulations": [{"task_id": "1", "trial": 0, "reward_info": {"reward": reward}, "messages": messages}],
    }


def fail_writes_of(data, tool):
    """Mark the result of every call to one write tool as an error, leaving the reads alone."""
    for sim in data["simulations"]:
        ids = {c["id"] for m in sim["messages"] for c in m.get("tool_calls") or [] if c["name"] == tool}
        for m in sim["messages"]:
            if m.get("role") == "tool" and m.get("id") in ids:
                m["error"] = True


def test_reads_in_the_gold_are_not_write_actions():
    t = task([("get_reservation_details", {"reservation_id": "R1"})])
    assert not in_gold(t, "get_reservation_details", {"reservation_id": "R1"})


def test_removed_actions_are_the_run_gold_writes_the_later_gold_lacks():
    run = task([("cancel_reservation", {"reservation_id": "R1"}), ("cancel_reservation", {"reservation_id": "R2"})])
    later = task([("cancel_reservation", {"reservation_id": "R2"})])
    assert [a["arguments"] for a in removed_actions(run, later)] == [{"reservation_id": "R1"}]
    assert removed_actions(later, later) == []


def test_argument_order_does_not_make_an_action_different():
    run = task([("update_reservation_baggages", {"a": 1, "b": 2})])
    later = task([("update_reservation_baggages", {"b": 2, "a": 1})])
    assert removed_actions(run, later) == []


def test_the_fixture_summary_counts_runs_writes_and_verdicts():
    data, later = fixture()
    s = evaluate_results(data, POLICY, later)
    assert (s["trajectories"], s["passed"], s["failed"]) == (3, 3, 0)
    assert s["writes_ran"] == 5 and s["writes_errored"] == 0
    assert dict(s["verdicts_ran"]) == {"allow": 1, "hold": 3, "block": 1}
    assert s["trajectories_with_block"] == {"passed": 1, "failed": 0}
    assert s["trajectories_with_hold"] == {"passed": 2, "failed": 0}


def test_a_rule_is_counted_once_per_call_even_with_a_finding_per_segment():
    res = reservation(
        "R1",
        cabin="business",
        flights=[
            {"flight_number": "HAT1", "origin": "JFK", "destination": "ATL", "date": "2024-05-13", "price": 100},
            {"flight_number": "HAT2", "origin": "ATL", "destination": "JFK", "date": "2024-05-14", "price": 100},
        ],
    )
    messages = [
        call("c1", "get_reservation_details", {"reservation_id": "R1"}),
        result("c1", json.dumps(res)),
        call("c2", "get_flight_status", {"flight_number": "HAT1", "date": "2024-05-13"}),
        result("c2", "landed"),
        call("c3", "get_flight_status", {"flight_number": "HAT2", "date": "2024-05-14"}),
        result("c3", "landed"),
        call("c4", "cancel_reservation", {"reservation_id": "R1"}),
        result("c4", json.dumps(reservation("R1", cabin="business", status="cancelled"))),
    ]
    s = evaluate_results(synthetic(messages), POLICY)
    # two flown segments give two findings of one rule on one call
    assert s["verdicts_ran"]["block"] == 1
    assert s["blocks_by_rule"]["cancel-eligibility"] == {"passed": 1, "failed": 0}


def test_blocks_in_passed_runs_say_whether_the_gold_expected_the_call():
    data, later = fixture()
    plain = to_plain(evaluate_results(data, POLICY, later))
    assert plain["blocks_in_passed_runs"] == [
        {"task": "29", "trial": 0, "tool": "update_reservation_flights", "rules": ["trip-endpoints"], "in_run_gold": True, "in_later_gold": False},
    ]


def test_the_later_gold_is_optional():
    data, _ = fixture()
    s = evaluate_results(data, POLICY)
    assert "in_later_gold" not in s["blocks_in_passed_runs"][0]
    assert s["removed_gold_actions"] == []


def test_removed_gold_actions_show_what_the_gate_said_about_each():
    data, later = fixture()
    plain = to_plain(evaluate_results(data, POLICY, later))
    by_key = {(r["task"], r["tool"]): r for r in plain["removed_gold_actions"]}
    assert by_key[("29", "update_reservation_flights")]["verdicts"] == {"allow": 0, "hold": 0, "block": 1}
    assert by_key[("37", "cancel_reservation")]["agent_calls"] == 1
    assert by_key[("37", "cancel_reservation")]["verdicts"] == {"allow": 0, "hold": 1, "block": 0}


def test_a_failed_call_is_counted_apart_from_one_that_ran():
    data, later = fixture()
    before = evaluate_results(data, POLICY, later)
    assert before["verdicts_ran"]["block"] == 1 and before["writes_errored"] == 0
    # only the flight change of task 29 fails; the reads before it still happen, so the gate still blocks it
    fail_writes_of(data, "update_reservation_flights")
    s = evaluate_results(data, POLICY, later)
    assert s["writes_errored"] == 2  # the flight changes of tasks 29 and 37
    assert s["writes_ran"] == before["writes_ran"] - 2
    assert s["verdicts_errored"]["block"] == 1
    assert s["verdicts_ran"]["block"] == 0
    assert s["blocks_in_passed_runs"] == []
    assert s["trajectories_with_block"] == {"passed": 0, "failed": 0}


@pytest.mark.parametrize("reward", [0.0, 0.5, 0.99])
def test_a_run_with_reward_below_one_is_a_failed_run(reward):
    data, later = fixture()
    data["simulations"][0]["reward_info"]["reward"] = reward
    s = evaluate_results(data, POLICY, later)
    assert (s["passed"], s["failed"]) == (2, 1)
    assert s["trajectories_with_block"]["failed"] == 1


def test_timings_stay_out_of_the_plain_summary():
    data, later = fixture()
    s = evaluate_results(data, POLICY, later)
    assert s["nanoseconds"]
    assert "nanoseconds" not in to_plain(s)


def test_the_table_groups_models_by_split():
    data, later = fixture()
    plain = {"m1": to_plain(evaluate_results(data, POLICY, later))}
    table = markdown_table(plain, {"development": ["m1"], "held_out": ["m2"]})
    rows = table.splitlines()
    assert len(rows) == 3  # header, rule, one model (m2 has no results)
    assert rows[2].startswith("| development | m1 | 3 (3 / 0) | 5 | 1 | 3 | 1 | 1 / 0 | 0 of 1 |")
