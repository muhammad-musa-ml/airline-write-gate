import pytest
from builders import change_reservation, flight, read_reservation, read_status, read_user, search_direct

from airline_write_gate.gate import Gate


def send(gate, amount, uid="u1"):
    return gate.evaluate("send_certificate", {"user_id": uid, "amount": amount})


def rules_of(decision):
    return [f.rule for f in decision.findings]


def two_passengers():
    return [{"first_name": "A", "last_name": "B", "dob": "1990-01-01"}, {"first_name": "C", "last_name": "D", "dob": "1991-01-01"}]


def test_a_certificate_for_a_user_nobody_read_is_held():
    decision = send(Gate(), 100)
    assert decision.verdict == "hold"
    assert rules_of(decision) == ["read-before-write"]


def test_with_no_reservation_read_the_certificate_is_held():
    gate = Gate()
    read_user(gate, membership="gold")
    decision = send(gate, 100)
    assert decision.verdict == "hold"
    assert "get_reservation_details" in decision.findings[0].repair


def test_a_regular_member_with_no_insurance_in_economy_is_blocked():
    gate = Gate()
    read_user(gate)
    read_reservation(gate)
    read_status(gate, "HAT1", "2024-05-20", "cancelled")
    decision = send(gate, 100)
    assert decision.verdict == "block"
    assert "silver or gold members, travel insurance, or business class" in decision.findings[0].repair


@pytest.mark.parametrize(
    "membership,insurance,cabin",
    [("silver", "no", "economy"), ("gold", "no", "basic_economy"), ("regular", "yes", "economy"), ("regular", "no", "business")],
)
def test_each_way_to_be_eligible_is_enough(membership, insurance, cabin):
    gate = Gate()
    read_user(gate, membership=membership)
    read_reservation(gate, insurance=insurance, cabin=cabin)
    read_status(gate, "HAT1", "2024-05-20", "cancelled")
    assert send(gate, 100).verdict == "allow"


def test_the_cancelled_amount_is_100_per_passenger():
    gate = Gate()
    read_user(gate, membership="gold")
    read_reservation(gate, passengers=two_passengers())
    read_status(gate, "HAT1", "2024-05-20", "cancelled")
    assert send(gate, 200).verdict == "allow"
    decision = send(gate, 100)
    assert decision.verdict == "block"
    assert "100 per passenger" in decision.findings[0].reason


def test_the_delayed_amount_is_50_per_passenger():
    gate = Gate()
    read_user(gate, membership="gold")
    read_reservation(gate, passengers=two_passengers())
    read_status(gate, "HAT1", "2024-05-20", "delayed")
    change_reservation(gate, passengers=two_passengers())
    assert send(gate, 100).verdict == "allow"
    assert send(gate, 200).verdict == "block"


def test_a_delayed_flight_is_compensated_only_after_the_reservation_is_changed():
    gate = Gate()
    read_user(gate, membership="gold")
    read_reservation(gate)
    read_status(gate, "HAT1", "2024-05-20", "delayed")
    decision = send(gate, 50)
    assert decision.verdict == "block"
    assert "has not changed or cancelled" in decision.findings[0].reason
    change_reservation(gate)
    assert send(gate, 50).verdict == "allow"


NEW_FLIGHTS = [{"flight_number": "HAT2", "origin": "JFK", "destination": "ATL", "date": "2024-05-21", "price": 100}]


def test_a_delayed_flight_is_still_compensated_after_a_change_that_replaces_the_flight():
    # the result of a real flights change holds the new flight, so the delayed one is no longer on the record
    gate = Gate()
    read_user(gate, membership="gold")
    read_reservation(gate)
    read_status(gate, "HAT1", "2024-05-20", "delayed")
    change_reservation(gate, flights=NEW_FLIGHTS)
    assert gate.session.reservations["R1"]["flights"][0]["flight_number"] == "HAT2"
    assert send(gate, 50).verdict == "allow"


def test_a_replaced_flight_that_was_on_time_does_not_back_a_delayed_amount():
    gate = Gate()
    read_user(gate, membership="gold")
    read_reservation(gate)
    read_status(gate, "HAT1", "2024-05-20", "on time")
    change_reservation(gate, flights=NEW_FLIGHTS)
    read_status(gate, "HAT2", "2024-05-21", "available")
    assert send(gate, 50).verdict == "block"


def test_a_cancellation_by_the_agent_also_counts_as_the_change():
    gate = Gate()
    read_user(gate, membership="gold")
    read_reservation(gate)
    read_status(gate, "HAT1", "2024-05-20", "delayed")
    change_reservation(gate, tool="cancel_reservation")
    assert send(gate, 50).verdict == "allow"


def test_a_change_that_failed_does_not_count():
    gate = Gate()
    read_user(gate, membership="gold")
    read_reservation(gate)
    read_status(gate, "HAT1", "2024-05-20", "delayed")
    change_reservation(gate, error=True)
    assert send(gate, 50).verdict == "block"


def test_a_baggage_change_is_not_a_change_of_the_reservation_for_this_rule():
    gate = Gate()
    read_user(gate, membership="gold")
    read_reservation(gate)
    read_status(gate, "HAT1", "2024-05-20", "delayed")
    change_reservation(gate, tool="update_reservation_baggages")
    assert send(gate, 50).verdict == "block"


def test_the_cancelled_amount_needs_no_change():
    gate = Gate()
    read_user(gate, membership="gold")
    read_reservation(gate)
    read_status(gate, "HAT1", "2024-05-20", "cancelled")
    assert send(gate, 100).verdict == "allow"


def test_an_amount_for_a_status_the_flight_does_not_have_is_blocked():
    gate = Gate()
    read_user(gate, membership="gold")
    read_reservation(gate)
    read_status(gate, "HAT1", "2024-05-20", "on time")
    assert send(gate, 100).verdict == "block"
    assert send(gate, 50).verdict == "block"


def test_a_certificate_before_any_flight_status_was_read_is_held():
    gate = Gate()
    read_user(gate, membership="gold")
    read_reservation(gate)
    decision = send(gate, 100)
    assert decision.verdict == "hold"
    assert "get_flight_status" in decision.findings[0].repair


def test_a_status_seen_in_a_search_counts_as_read():
    gate = Gate()
    read_user(gate, membership="gold")
    read_reservation(gate)
    search_direct(gate, "2024-05-20", [flight("HAT1", status="available")])
    assert send(gate, 100).verdict == "block"


def test_another_users_reservation_does_not_make_this_user_eligible():
    gate = Gate()
    read_user(gate)
    read_reservation(gate, user_id="someone-else", cabin="business")
    read_status(gate, "HAT1", "2024-05-20", "cancelled")
    assert send(gate, 100).verdict == "hold"


# ---- booking limits ----


def book(passengers, payments):
    people = [{"first_name": str(i), "last_name": "X", "dob": "1990-01-01"} for i in range(passengers)]
    return Gate().evaluate(
        "book_reservation",
        {
            "user_id": "u1",
            "origin": "JFK",
            "destination": "ATL",
            "flight_type": "one_way",
            "cabin": "economy",
            "flights": [],
            "passengers": people,
            "payment_methods": [{"payment_id": p, "amount": 10} for p in payments],
            "total_baggages": 0,
            "nonfree_baggages": 0,
            "insurance": "no",
        },
    )


def booking_findings(decision):
    return [f for f in decision.findings if f.rule == "booking-limits"]


def booking_rules(decision):
    return [f.rule for f in booking_findings(decision)]


def test_five_passengers_are_the_most_one_reservation_holds():
    assert booking_rules(book(5, ["credit_card_1"])) == []
    decision = book(6, ["credit_card_1"])
    assert booking_rules(decision) == ["booking-limits"]
    assert decision.verdict == "block"


def test_one_credit_card_one_certificate_and_three_gift_cards_are_the_most():
    assert booking_rules(book(1, ["credit_card_1", "certificate_1", "gift_card_1", "gift_card_2", "gift_card_3"])) == []


@pytest.mark.parametrize(
    "payments,kind",
    [
        (["credit_card_1", "credit_card_2"], "credit_card"),
        (["certificate_1", "certificate_2"], "certificate"),
        (["gift_card_1", "gift_card_2", "gift_card_3", "gift_card_4"], "gift_card"),
    ],
)
def test_one_payment_type_over_its_limit_is_blocked(payments, kind):
    decision = book(1, payments)
    assert booking_rules(decision) == ["booking-limits"]
    assert decision.verdict == "block"
    assert kind in booking_findings(decision)[0].reason
