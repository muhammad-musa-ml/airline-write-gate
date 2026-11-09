import pytest
from builders import flight, read_reservation, read_user, search_direct

from airline_write_gate.gate import Gate, load_policy


def change_flights(gate, flights, cabin="economy", rid="R1"):
    return gate.evaluate(
        "update_reservation_flights",
        {"reservation_id": rid, "cabin": cabin, "flights": flights, "payment_id": "credit_card_1"},
    )


def rules_of(decision):
    return [f.rule for f in decision.findings]


NEW = [{"flight_number": "HAT2", "date": "2024-05-21"}]
OLD = [{"flight_number": "HAT1", "date": "2024-05-20"}]


def gate_with_search(**reservation_overrides):
    gate = Gate()
    read_reservation(gate, **reservation_overrides)
    search_direct(gate, "2024-05-21", [flight("HAT2")])
    return gate


# ---- flights-seen-available ----


def test_a_new_flight_no_search_listed_is_held():
    gate = Gate()
    read_reservation(gate)
    decision = change_flights(gate, NEW)
    assert decision.verdict == "hold"
    assert rules_of(decision) == ["flights-seen-available"]
    assert "HAT2 on 2024-05-21" in decision.findings[0].reason
    assert "search_direct_flight" in decision.findings[0].repair


def test_a_searched_available_flight_passes():
    assert change_flights(gate_with_search(), NEW).verdict == "allow"


def test_the_search_date_matters():
    gate = Gate()
    read_reservation(gate)
    search_direct(gate, "2024-05-22", [flight("HAT2")])
    assert change_flights(gate, NEW).verdict == "hold"


def test_a_flight_that_is_not_available_is_blocked():
    gate = Gate()
    read_reservation(gate)
    search_direct(gate, "2024-05-21", [flight("HAT2", status="delayed")])
    decision = change_flights(gate, NEW)
    assert decision.verdict == "block"
    assert "HAT2 on 2024-05-21 is delayed" in decision.findings[0].reason


def test_too_few_seats_in_the_cabin_is_blocked():
    gate = Gate()
    read_reservation(gate)
    search_direct(gate, "2024-05-21", [flight("HAT2", available_seats={"basic_economy": 0, "economy": 0, "business": 5})])
    decision = change_flights(gate, NEW)
    assert decision.verdict == "block"
    assert "0 economy seats for 1 passengers" in decision.findings[0].reason


def test_seats_are_counted_against_the_passengers_on_the_reservation():
    passengers = [{"first_name": "A", "last_name": "B", "dob": "1990-01-01"}, {"first_name": "C", "last_name": "D", "dob": "1991-01-01"}]
    gate = Gate()
    read_reservation(gate, passengers=passengers)
    search_direct(gate, "2024-05-21", [flight("HAT2", available_seats={"basic_economy": 1, "economy": 1, "business": 1})])
    assert change_flights(gate, NEW).verdict == "block"


def test_a_kept_flight_in_the_same_cabin_needs_no_search():
    gate = Gate()
    read_reservation(gate)
    assert change_flights(gate, OLD).verdict == "allow"


def test_a_kept_flight_in_a_new_cabin_is_repriced_so_it_needs_a_search():
    gate = Gate()
    read_reservation(gate)
    decision = change_flights(gate, OLD, cabin="business")
    assert decision.verdict == "hold"
    assert rules_of(decision) == ["flights-seen-available"]


# ---- flight-change-basic-economy ----


def test_changing_the_flights_of_a_basic_economy_reservation_is_blocked():
    gate = gate_with_search(cabin="basic_economy")
    decision = change_flights(gate, NEW, cabin="basic_economy")
    assert decision.verdict == "block"
    assert rules_of(decision) == ["flight-change-basic-economy"]


def test_a_cabin_only_change_on_basic_economy_is_allowed_by_this_rule():
    gate = Gate()
    read_reservation(gate, cabin="basic_economy")
    search_direct(gate, "2024-05-20", [flight("HAT1")])
    decision = change_flights(gate, OLD, cabin="economy")
    assert "flight-change-basic-economy" not in rules_of(decision)
    assert decision.verdict == "allow"


def test_the_same_flights_in_another_order_are_not_a_change():
    two = [
        {"flight_number": "HAT1", "origin": "JFK", "destination": "ATL", "date": "2024-05-20", "price": 100},
        {"flight_number": "HAT5", "origin": "ATL", "destination": "JFK", "date": "2024-05-25", "price": 100},
    ]
    gate = Gate()
    read_reservation(gate, cabin="basic_economy", flights=two, flight_type="round_trip")
    search_direct(gate, "2024-05-20", [flight("HAT1")])
    search_direct(gate, "2024-05-25", [flight("HAT5")])
    reordered = [{"flight_number": "HAT5", "date": "2024-05-25"}, {"flight_number": "HAT1", "date": "2024-05-20"}]
    assert "flight-change-basic-economy" not in rules_of(change_flights(gate, reordered, cabin="economy"))


# ---- cabin-change-after-flown ----


FLOWN = [{"flight_number": "HAT1", "origin": "JFK", "destination": "ATL", "date": "2024-05-10", "price": 100}]


def test_a_cabin_change_after_a_flown_segment_is_blocked():
    gate = Gate()
    read_reservation(gate, flights=FLOWN)
    search_direct(gate, "2024-05-10", [flight("HAT1")])
    decision = change_flights(gate, [{"flight_number": "HAT1", "date": "2024-05-10"}], cabin="business")
    assert decision.verdict == "block"
    assert "cabin-change-after-flown" in rules_of(decision)


def test_a_flown_segment_does_not_matter_when_the_cabin_stays_the_same():
    gate = Gate()
    read_reservation(gate, flights=FLOWN)
    assert "cabin-change-after-flown" not in rules_of(change_flights(gate, [{"flight_number": "HAT1", "date": "2024-05-10"}]))


def test_a_cabin_change_with_a_flight_today_and_no_status_is_held():
    today = [{"flight_number": "HAT1", "origin": "JFK", "destination": "ATL", "date": "2024-05-15", "price": 100}]
    gate = Gate()
    read_reservation(gate, flights=today)
    search_direct(gate, "2024-05-15", [flight("HAT1")])
    decision = change_flights(gate, [{"flight_number": "HAT1", "date": "2024-05-15"}], cabin="business")
    assert decision.verdict == "hold"
    assert "get_flight_status" in decision.findings[0].repair


def test_a_cabin_change_before_anything_is_flown_passes_this_rule():
    gate = Gate()
    read_reservation(gate)
    search_direct(gate, "2024-05-20", [flight("HAT1")])
    decision = change_flights(gate, OLD, cabin="business")
    assert decision.verdict == "allow"


# ---- trip-endpoints ----


def test_new_flights_that_end_somewhere_else_are_blocked():
    gate = Gate()
    read_reservation(gate)
    search_direct(gate, "2024-05-21", [flight("HAT2", destination="PHX")])
    decision = change_flights(gate, NEW)
    assert decision.verdict == "block"
    assert rules_of(decision) == ["trip-endpoints"]
    assert "JFK to PHX" in decision.findings[0].reason


def test_new_flights_that_start_somewhere_else_are_blocked():
    gate = Gate()
    read_reservation(gate)
    search_direct(gate, "2024-05-21", [flight("HAT2", origin="LGA")])
    assert rules_of(change_flights(gate, NEW)) == ["trip-endpoints"]


def test_a_connection_with_the_same_ends_passes():
    gate = Gate()
    read_reservation(gate)
    search_direct(gate, "2024-05-21", [flight("HAT2", destination="PHX"), flight("HAT3", origin="PHX", destination="ATL")])
    legs = [{"flight_number": "HAT2", "date": "2024-05-21"}, {"flight_number": "HAT3", "date": "2024-05-21"}]
    assert change_flights(gate, legs).verdict == "allow"


def test_a_round_trip_must_come_home_and_visit_the_destination():
    out = {"flight_number": "HAT1", "origin": "JFK", "destination": "ATL", "date": "2024-05-20", "price": 100}
    back = {"flight_number": "HAT5", "origin": "ATL", "destination": "JFK", "date": "2024-05-25", "price": 100}
    gate = Gate()
    read_reservation(gate, flight_type="round_trip", flights=[out, back])
    search_direct(gate, "2024-05-26", [flight("HAT6", origin="ATL", destination="JFK")])
    good = [{"flight_number": "HAT1", "date": "2024-05-20"}, {"flight_number": "HAT6", "date": "2024-05-26"}]
    assert change_flights(gate, good).verdict == "allow"
    search_direct(gate, "2024-05-27", [flight("HAT7", origin="ATL", destination="PHX")])
    bad = [{"flight_number": "HAT1", "date": "2024-05-20"}, {"flight_number": "HAT7", "date": "2024-05-27"}]
    assert rules_of(change_flights(gate, bad)) == ["trip-endpoints"]


def test_endpoints_are_not_judged_for_a_flight_no_search_listed():
    gate = Gate()
    read_reservation(gate)
    assert rules_of(change_flights(gate, NEW)) == ["flights-seen-available"]


# ---- baggage ----


def change_bags(gate, total, nonfree, rid="R1"):
    return gate.evaluate(
        "update_reservation_baggages",
        {"reservation_id": rid, "total_baggages": total, "nonfree_baggages": nonfree, "payment_id": "credit_card_1"},
    )


def test_lowering_the_checked_bags_is_blocked():
    gate = Gate()
    read_reservation(gate, total_baggages=3, nonfree_baggages=2)
    read_user(gate)
    decision = change_bags(gate, 1, 0)
    assert decision.verdict == "block"
    assert rules_of(decision) == ["baggage-no-removal"]


def test_adding_bags_with_the_right_non_free_count_passes():
    gate = Gate()
    read_reservation(gate, total_baggages=1, nonfree_baggages=0)
    read_user(gate)  # regular member, economy: one free bag
    assert change_bags(gate, 3, 2).verdict == "allow"


def test_a_wrong_non_free_count_is_blocked_with_the_right_one_in_the_repair():
    gate = Gate()
    read_reservation(gate)
    read_user(gate)
    decision = change_bags(gate, 3, 3)
    assert decision.verdict == "block"
    assert rules_of(decision) == ["baggage-allowance"]
    assert decision.findings[0].repair == "set nonfree_baggages to 2"


@pytest.mark.parametrize(
    "membership,cabin,passengers,total,expected",
    [
        ("regular", "basic_economy", 1, 2, 2),
        ("regular", "economy", 1, 2, 1),
        ("regular", "business", 1, 2, 0),
        ("silver", "basic_economy", 1, 2, 1),
        ("silver", "economy", 2, 5, 1),
        ("gold", "economy", 1, 3, 0),
        ("gold", "business", 2, 8, 0),
        ("gold", "business", 2, 9, 1),
    ],
)
def test_the_free_allowance_follows_member_level_cabin_and_passengers(membership, cabin, passengers, total, expected):
    people = [{"first_name": str(i), "last_name": "X", "dob": "1990-01-01"} for i in range(passengers)]
    gate = Gate()
    read_reservation(gate, cabin=cabin, passengers=people)
    read_user(gate, membership=membership)
    assert change_bags(gate, total, expected).verdict == "allow"
    assert change_bags(gate, total, expected + 1).verdict == "block"


def test_the_bag_count_is_held_when_the_member_level_was_never_read():
    gate = Gate()
    read_reservation(gate)
    decision = change_bags(gate, 2, 1)
    assert decision.verdict == "hold"
    assert "get_user_details" in decision.findings[0].repair


def test_a_policy_without_the_allowance_table_is_refused(tmp_path):
    policy = load_policy()
    del policy["rules"]["baggage-allowance"]["free_bags"]
    import json

    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(ValueError, match="free_bags"):
        load_policy(path)


# ---- booking ----


def book(gate, flights, cabin="economy", passengers=1, total=0, nonfree=0, uid="u1"):
    people = [{"first_name": str(i), "last_name": "X", "dob": "1990-01-01"} for i in range(passengers)]
    return gate.evaluate(
        "book_reservation",
        {
            "user_id": uid,
            "origin": "JFK",
            "destination": "ATL",
            "flight_type": "one_way",
            "cabin": cabin,
            "flights": flights,
            "passengers": people,
            "payment_methods": [{"payment_id": "credit_card_1", "amount": 100}],
            "total_baggages": total,
            "nonfree_baggages": nonfree,
            "insurance": "no",
        },
    )


def test_a_booking_from_a_flight_no_search_listed_is_held():
    gate = Gate()
    read_user(gate)
    decision = book(gate, OLD)
    assert decision.verdict == "hold"
    assert rules_of(decision) == ["flights-seen-available"]


def test_a_booking_from_searched_flights_with_a_right_bag_count_passes():
    gate = Gate()
    read_user(gate)
    search_direct(gate, "2024-05-20", [flight("HAT1")])
    assert book(gate, OLD, total=1, nonfree=0).verdict == "allow"


def test_a_booking_counts_seats_against_its_own_passengers():
    gate = Gate()
    read_user(gate)
    search_direct(gate, "2024-05-20", [flight("HAT1", available_seats={"basic_economy": 1, "economy": 2, "business": 1})])
    assert book(gate, OLD, passengers=2).verdict == "allow"
    assert book(gate, OLD, passengers=3).verdict == "block"


def test_a_booking_uses_the_members_allowance_for_its_own_cabin():
    gate = Gate()
    read_user(gate, membership="gold")
    search_direct(gate, "2024-05-20", [flight("HAT1")])
    assert book(gate, OLD, cabin="economy", total=3, nonfree=0).verdict == "allow"
    assert book(gate, OLD, cabin="economy", total=3, nonfree=1).verdict == "block"
