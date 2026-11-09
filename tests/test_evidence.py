import json

from builders import flight, reservation

from airline_write_gate.evidence import Session


def test_reservation_read_is_stored_by_id():
    session = Session()
    session.observe("get_reservation_details", {"reservation_id": "R1"}, json.dumps(reservation("R1")))
    assert session.reservations["R1"]["cabin"] == "economy"
    assert "R2" not in session.reservations


def test_user_read_is_stored_by_id():
    session = Session()
    user = {"user_id": "u1", "membership": "gold", "payment_methods": {}}
    session.observe("get_user_details", {"user_id": "u1"}, json.dumps(user))
    assert session.users["u1"]["membership"] == "gold"


def test_failed_call_records_nothing():
    session = Session()
    session.observe("get_reservation_details", {"reservation_id": "R1"}, json.dumps(reservation("R1")), error=True)
    assert session.reservations == {}


def test_text_that_is_not_json_records_nothing():
    session = Session()
    session.observe("get_reservation_details", {"reservation_id": "R1"}, "Error: reservation not found")
    session.observe("get_user_details", {"user_id": "u1"}, "Error: user not found")
    assert session.reservations == {} and session.users == {}


def test_write_result_replaces_the_stored_reservation():
    session = Session()
    session.observe("get_reservation_details", {"reservation_id": "R1"}, json.dumps(reservation("R1")))
    after = reservation("R1", total_baggages=2, nonfree_baggages=1)
    session.observe("update_reservation_baggages", {"reservation_id": "R1"}, json.dumps(after))
    assert session.reservations["R1"]["total_baggages"] == 2


def test_booking_result_adds_the_new_reservation():
    session = Session()
    session.observe("book_reservation", {"user_id": "u1"}, json.dumps(reservation("NEW123")))
    assert "NEW123" in session.reservations


def test_direct_search_takes_the_date_from_the_search_arguments():
    session = Session()
    results = [flight("HAT1"), flight("HAT2")]
    session.observe("search_direct_flight", {"origin": "JFK", "destination": "ATL", "date": "2024-05-20"}, json.dumps(results))
    assert ("HAT1", "2024-05-20") in session.flights
    assert ("HAT2", "2024-05-20") in session.flights
    assert ("HAT1", "2024-05-21") not in session.flights


def test_onestop_search_keeps_every_leg_with_its_own_date():
    session = Session()
    leg_one = flight("HAT3", "2024-05-24")
    leg_two = flight("HAT4", "2024-05-25", origin="ATL", destination="PHL")
    session.observe("search_onestop_flight", {"origin": "JFK", "destination": "PHL", "date": "2024-05-24"}, json.dumps([[leg_one, leg_two]]))
    assert session.flights[("HAT3", "2024-05-24")]["destination"] == "ATL"
    assert session.flights[("HAT4", "2024-05-25")]["destination"] == "PHL"
    assert ("HAT4", "2024-05-24") not in session.flights


def test_the_flights_a_change_took_off_a_reservation_are_still_remembered():
    session = Session()
    session.observe("get_reservation_details", {"reservation_id": "R1"}, json.dumps(reservation("R1")))
    new = [{"flight_number": "HAT2", "origin": "JFK", "destination": "ATL", "date": "2024-05-21", "price": 100}]
    session.observe("update_reservation_flights", {"reservation_id": "R1"}, json.dumps(reservation("R1", flights=new)))
    assert [f["flight_number"] for f in session.reservations["R1"]["flights"]] == ["HAT2"]
    assert sorted(f["flight_number"] for f in session.flights_ever_on("R1")) == ["HAT1", "HAT2"]
    assert session.flights_ever_on("R9") == []


def test_a_later_search_replaces_the_earlier_record_of_the_same_flight():
    session = Session()
    search = {"origin": "JFK", "destination": "ATL", "date": "2024-05-20"}
    session.observe("search_direct_flight", search, json.dumps([flight("HAT1", prices={"economy": 100})]))
    session.observe("search_direct_flight", search, json.dumps([flight("HAT1", prices={"economy": 120})]))
    assert session.flights[("HAT1", "2024-05-20")]["prices"]["economy"] == 120


def test_flight_status_is_stored_by_flight_and_date():
    session = Session()
    session.observe("get_flight_status", {"flight_number": "HAT9", "date": "2024-05-15"}, "cancelled")
    assert session.statuses[("HAT9", "2024-05-15")] == "cancelled"
    assert ("HAT9", "2024-05-16") not in session.statuses


def test_a_cancel_or_flight_change_that_ran_marks_the_reservation_changed():
    session = Session()
    session.observe("cancel_reservation", {"reservation_id": "R1"}, json.dumps(reservation("R1")))
    session.observe("update_reservation_flights", {"reservation_id": "R2"}, json.dumps(reservation("R2")))
    assert session.changed == {"R1", "R2"}


def test_a_failed_change_and_a_baggage_or_passenger_update_do_not_mark_it_changed():
    session = Session()
    session.observe("cancel_reservation", {"reservation_id": "R1"}, "Error", error=True)
    session.observe("update_reservation_baggages", {"reservation_id": "R2"}, json.dumps(reservation("R2")))
    session.observe("update_reservation_passengers", {"reservation_id": "R3"}, json.dumps(reservation("R3")))
    assert session.changed == set()
