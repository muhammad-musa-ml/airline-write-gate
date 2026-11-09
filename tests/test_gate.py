import json

import pytest
from builders import read_reservation, read_status

from airline_write_gate.gate import Gate, load_policy


def verdict(gate, name, **arguments):
    return gate.evaluate(name, arguments)


def test_reads_are_never_gated():
    gate = Gate()
    decision = verdict(gate, "get_reservation_details", reservation_id="R1")
    assert decision.verdict == "allow" and decision.findings == ()


def test_cancel_of_an_unread_reservation_is_held_with_the_read_as_the_repair():
    gate = Gate()
    decision = verdict(gate, "cancel_reservation", reservation_id="R9")
    assert decision.verdict == "hold"
    assert decision.findings[0].rule == "read-before-write"
    assert "get_reservation_details" in decision.findings[0].repair
    assert "R9" in decision.findings[0].repair


def test_a_reservation_read_in_another_gate_does_not_count():
    first, second = Gate(), Gate()
    read_reservation(first, cabin="business")
    assert verdict(first, "cancel_reservation", reservation_id="R1").verdict == "allow"
    assert verdict(second, "cancel_reservation", reservation_id="R1").verdict == "hold"


def test_business_cabin_may_be_cancelled():
    gate = Gate()
    read_reservation(gate, cabin="business")
    assert verdict(gate, "cancel_reservation", reservation_id="R1").verdict == "allow"


def test_booked_within_24_hours_may_be_cancelled():
    gate = Gate()
    read_reservation(gate, created_at="2024-05-14T16:00:00")
    assert verdict(gate, "cancel_reservation", reservation_id="R1").verdict == "allow"


def test_booked_one_second_outside_the_window_is_not_inside_it():
    gate = Gate()
    read_reservation(gate, created_at="2024-05-14T14:59:59")
    read_status(gate, "HAT1", "2024-05-20", "available")
    decision = verdict(gate, "cancel_reservation", reservation_id="R1")
    assert decision.verdict == "block"
    assert decision.findings[0].rule == "cancel-eligibility"


def test_window_is_a_policy_parameter():
    policy = load_policy()
    policy["rules"]["cancel-eligibility"]["window_hours"] = 48
    gate = Gate(policy)
    read_reservation(gate, created_at="2024-05-14T14:59:59")
    assert verdict(gate, "cancel_reservation", reservation_id="R1").verdict == "allow"


def test_insurance_is_enough_even_though_the_reason_is_not_checked():
    gate = Gate()
    read_reservation(gate, insurance="yes")
    assert verdict(gate, "cancel_reservation", reservation_id="R1").verdict == "allow"


def test_economy_without_insurance_is_held_until_flight_statuses_are_read():
    gate = Gate()
    read_reservation(gate)
    decision = verdict(gate, "cancel_reservation", reservation_id="R1")
    assert decision.verdict == "hold"
    assert "get_flight_status" in decision.findings[0].repair


def test_economy_without_insurance_is_blocked_once_no_flight_is_cancelled():
    gate = Gate()
    read_reservation(gate)
    read_status(gate, "HAT1", "2024-05-20", "available")
    decision = verdict(gate, "cancel_reservation", reservation_id="R1")
    assert decision.verdict == "block"
    assert "none of the four conditions" in decision.findings[0].repair


def test_a_flight_the_airline_cancelled_allows_the_cancellation():
    gate = Gate()
    read_reservation(gate)
    read_status(gate, "HAT1", "2024-05-20", "cancelled")
    assert verdict(gate, "cancel_reservation", reservation_id="R1").verdict == "allow"


def test_a_flown_segment_blocks_even_a_business_reservation():
    gate = Gate()
    read_reservation(
        gate,
        cabin="business",
        flights=[{"flight_number": "HAT1", "origin": "JFK", "destination": "ATL", "date": "2024-05-10", "price": 300}],
    )
    decision = verdict(gate, "cancel_reservation", reservation_id="R1")
    assert decision.verdict == "block"
    assert "already been flown" in decision.findings[0].reason


def test_a_past_flight_the_airline_cancelled_was_not_flown():
    gate = Gate()
    read_reservation(
        gate,
        flights=[{"flight_number": "HAT1", "origin": "JFK", "destination": "ATL", "date": "2024-05-10", "price": 100}],
    )
    read_status(gate, "HAT1", "2024-05-10", "cancelled")
    assert verdict(gate, "cancel_reservation", reservation_id="R1").verdict == "allow"


def test_a_flight_today_with_no_status_read_is_held():
    gate = Gate()
    read_reservation(
        gate,
        cabin="business",
        flights=[{"flight_number": "HAT1", "origin": "JFK", "destination": "ATL", "date": "2024-05-15", "price": 300}],
    )
    decision = verdict(gate, "cancel_reservation", reservation_id="R1")
    assert decision.verdict == "hold"
    assert "departs today" in decision.findings[0].reason


@pytest.mark.parametrize("status,expected", [("landed", "block"), ("flying", "block"), ("delayed", "allow"), ("on time", "allow")])
def test_a_flight_today_is_flown_only_when_its_status_says_so(status, expected):
    gate = Gate()
    read_reservation(
        gate,
        cabin="business",
        flights=[{"flight_number": "HAT1", "origin": "JFK", "destination": "ATL", "date": "2024-05-15", "price": 300}],
    )
    read_status(gate, "HAT1", "2024-05-15", status)
    assert verdict(gate, "cancel_reservation", reservation_id="R1").verdict == expected


def test_a_rule_can_be_switched_off_in_the_policy():
    policy = load_policy()
    policy["rules"]["cancel-eligibility"]["enabled"] = False
    gate = Gate(policy)
    read_reservation(gate)
    assert verdict(gate, "cancel_reservation", reservation_id="R1").verdict == "allow"


def test_a_decision_tells_the_agent_what_to_do():
    gate = Gate()
    read_reservation(gate)
    read_status(gate, "HAT1", "2024-05-20", "available")
    sent = verdict(gate, "cancel_reservation", reservation_id="R1").to_agent()
    assert sent["executed"] is False
    assert sent["verdict"] == "block"
    assert set(sent["findings"][0]) == {"rule", "reason", "repair"}
    json.dumps(sent)


def test_policy_with_a_repeated_key_is_refused(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text('{"now": "2024-05-15T15:00:00", "rules": {"cancel-eligibility": {}, "cancel-eligibility": {}}}', encoding="utf-8")
    with pytest.raises(ValueError, match="repeated key"):
        load_policy(path)


def test_policy_naming_a_rule_that_does_not_exist_is_refused(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text('{"now": "2024-05-15T15:00:00", "rules": {"no-such-rule": {}}}', encoding="utf-8")
    with pytest.raises(ValueError, match="no-such-rule"):
        load_policy(path)
