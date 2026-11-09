"""Small builders for reservations, flights and users in the shape the tau2-bench tools return."""

import json


def reservation(rid="R1", **overrides):
    record = {
        "reservation_id": rid,
        "user_id": "u1",
        "origin": "JFK",
        "destination": "ATL",
        "flight_type": "one_way",
        "cabin": "economy",
        "flights": [{"flight_number": "HAT1", "origin": "JFK", "destination": "ATL", "date": "2024-05-20", "price": 100}],
        "passengers": [{"first_name": "A", "last_name": "B", "dob": "1990-01-01"}],
        "payment_history": [{"payment_id": "credit_card_1", "amount": 100}],
        "created_at": "2024-05-01T10:00:00",
        "total_baggages": 0,
        "nonfree_baggages": 0,
        "insurance": "no",
        "status": None,
    }
    record.update(overrides)
    return record


def flight(number, date=None, **overrides):
    record = {
        "flight_number": number,
        "origin": "JFK",
        "destination": "ATL",
        "status": "available",
        "date": date,
        "available_seats": {"basic_economy": 3, "economy": 3, "business": 3},
        "prices": {"basic_economy": 50, "economy": 100, "business": 300},
    }
    record.update(overrides)
    return record


def user(uid="u1", membership="regular", **overrides):
    record = {
        "user_id": uid,
        "name": {"first_name": "A", "last_name": "B"},
        "membership": membership,
        "payment_methods": {
            "credit_card_1": {"source": "credit_card", "id": "credit_card_1"},
            "gift_card_1": {"source": "gift_card", "id": "gift_card_1", "amount": 200.0},
            "certificate_1": {"source": "certificate", "id": "certificate_1", "amount": 100.0},
        },
        "saved_passengers": [],
        "reservations": ["R1"],
    }
    record.update(overrides)
    return record


def read_reservation(gate, **overrides):
    record = reservation(**overrides)
    gate.observe("get_reservation_details", {"reservation_id": record["reservation_id"]}, json.dumps(record))
    return record


def read_user(gate, **overrides):
    record = user(**overrides)
    gate.observe("get_user_details", {"user_id": record["user_id"]}, json.dumps(record))
    return record


def change_reservation(gate, rid="R1", tool="update_reservation_flights", error=False, **overrides):
    """Record that the agent's write on a reservation ran (or failed); the result is the reservation after it."""
    gate.observe(tool, {"reservation_id": rid}, json.dumps(reservation(rid, **overrides)), error)


def read_status(gate, number, date, status):
    gate.observe("get_flight_status", {"flight_number": number, "date": date}, status)


def search_direct(gate, date, flights):
    gate.observe(
        "search_direct_flight",
        {"origin": "JFK", "destination": "ATL", "date": date},
        json.dumps(flights),
    )
