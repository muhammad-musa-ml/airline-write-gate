import json
from pathlib import Path

from builders import call, flight, reservation, result

from airline_write_gate.gate import load_policy
from airline_write_gate.replay import replay_simulation

FIXTURES = Path(__file__).parent / "fixtures"
POLICY = load_policy()


def fixture_runs():
    data = json.loads((FIXTURES / "runs.json").read_text(encoding="utf-8"))
    return {(str(s["task_id"]), s["trial"]): s for s in data["simulations"]}


def sim(messages):
    return {"task_id": "t", "trial": 0, "messages": messages}


def test_a_run_where_every_write_is_supported_gets_no_block_and_no_hold():
    run = fixture_runs()[("40", 3)]
    records = replay_simulation(run, POLICY)
    assert [(r.tool, r.decision.verdict) for r in records] == [("update_reservation_passengers", "allow")]


def test_the_destination_change_in_task_29_is_blocked_in_shadow_mode():
    records = replay_simulation(fixture_runs()[("29", 0)], POLICY)
    flights = [r for r in records if r.tool == "update_reservation_flights"]
    assert [r.decision.verdict for r in flights] == ["block"]
    assert flights[0].decision.findings[0].rule == "trip-endpoints"
    assert not flights[0].errored  # the recorded call ran; shadow mode only reports


def test_the_cancellation_in_task_37_is_held_because_the_agent_never_read_the_flight_statuses():
    records = replay_simulation(fixture_runs()[("37", 1)], POLICY)
    cancels = [r for r in records if r.tool == "cancel_reservation"]
    assert [r.decision.verdict for r in cancels] == ["hold"]
    assert "has not been read" in cancels[0].decision.findings[0].reason
    assert "get_flight_status" in cancels[0].decision.findings[0].repair


def test_a_cancellation_after_the_statuses_were_read_as_landed_is_blocked_once_per_flown_segment():
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
    records = replay_simulation(sim(messages), POLICY)
    assert [r.decision.verdict for r in records] == ["block"]
    assert [f.reason for f in records[0].decision.findings] == [
        "HAT1 on 2024-05-13 has already been flown",
        "HAT2 on 2024-05-14 has already been flown",
    ]


def test_reads_produce_no_records():
    records = replay_simulation(fixture_runs()[("29", 0)], POLICY)
    assert {r.tool for r in records} <= {"update_reservation_flights", "update_reservation_baggages", "cancel_reservation", "book_reservation", "update_reservation_passengers", "send_certificate"}


def test_a_write_is_judged_before_its_own_result_is_seen():
    res = reservation("R1", cabin="business")
    messages = [
        call("c1", "get_reservation_details", {"reservation_id": "R1"}),
        result("c1", json.dumps(res)),
        call("c2", "cancel_reservation", {"reservation_id": "R1"}),
        result("c2", json.dumps(reservation("R1", cabin="business", status="cancelled"))),
        call("c3", "cancel_reservation", {"reservation_id": "R2"}),
        result("c3", "Error: reservation not found", error=True),
    ]
    records = replay_simulation(sim(messages), POLICY)
    assert [(r.tool, r.decision.verdict, r.errored) for r in records] == [
        ("cancel_reservation", "allow", False),
        ("cancel_reservation", "hold", True),
    ]


def test_a_write_with_no_read_before_it_is_held_even_if_it_ran():
    # a business reservation would be allowed if the gate had seen the write's own result first
    messages = [
        call("c1", "cancel_reservation", {"reservation_id": "R1"}),
        result("c1", json.dumps(reservation("R1", cabin="business"))),
    ]
    records = replay_simulation(sim(messages), POLICY)
    assert records[0].decision.verdict == "hold"
    assert [f.rule for f in records[0].decision.findings] == ["read-before-write"]
    assert records[0].errored is False


def test_what_a_write_returned_is_known_to_the_next_write():
    res = reservation("R1", total_baggages=0)
    after = reservation("R1", total_baggages=2, nonfree_baggages=1)
    user = {"user_id": "u1", "membership": "regular", "payment_methods": {}}
    messages = [
        call("c0", "get_user_details", {"user_id": "u1"}),
        result("c0", json.dumps(user)),
        call("c1", "get_reservation_details", {"reservation_id": "R1"}),
        result("c1", json.dumps(res)),
        call("c2", "update_reservation_baggages", {"reservation_id": "R1", "total_baggages": 2, "nonfree_baggages": 1, "payment_id": "x"}),
        result("c2", json.dumps(after)),
        call("c3", "update_reservation_baggages", {"reservation_id": "R1", "total_baggages": 1, "nonfree_baggages": 0, "payment_id": "x"}),
        result("c3", json.dumps(reservation("R1", total_baggages=1))),
    ]
    records = replay_simulation(sim(messages), POLICY)
    assert [r.decision.verdict for r in records] == ["allow", "block"]
    assert records[1].decision.findings[0].rule == "baggage-no-removal"


def test_calls_made_by_the_user_are_not_the_agents_writes():
    messages = [
        call("c1", "cancel_reservation", {"reservation_id": "R1"}, requestor="user"),
        result("c1", "ok"),
    ]
    assert replay_simulation(sim(messages), POLICY) == []


def test_search_results_feed_the_later_booking_check():
    user = {"user_id": "u1", "membership": "regular", "payment_methods": {}}
    found = [flight("HAT1")]
    book = {
        "user_id": "u1", "origin": "JFK", "destination": "ATL", "flight_type": "one_way", "cabin": "economy",
        "flights": [{"flight_number": "HAT1", "date": "2024-05-20"}],
        "passengers": [{"first_name": "A", "last_name": "B", "dob": "1990-01-01"}],
        "payment_methods": [{"payment_id": "credit_card_1", "amount": 100}],
        "total_baggages": 1, "nonfree_baggages": 0, "insurance": "no",
    }
    messages = [
        call("c0", "get_user_details", {"user_id": "u1"}),
        result("c0", json.dumps(user)),
        call("c1", "search_direct_flight", {"origin": "JFK", "destination": "ATL", "date": "2024-05-20"}),
        result("c1", json.dumps(found)),
        call("c2", "book_reservation", book),
        result("c2", json.dumps(reservation("NEW1"))),
    ]
    records = replay_simulation(sim(messages), POLICY)
    assert [r.decision.verdict for r in records] == ["allow"]


def test_every_record_carries_the_time_the_check_took():
    records = replay_simulation(fixture_runs()[("29", 0)], POLICY)
    assert all(r.nanoseconds > 0 for r in records)
