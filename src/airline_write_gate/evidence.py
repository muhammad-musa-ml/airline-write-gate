"""What an agent has read so far in one session.

The gate never looks at the airline database. It only knows what the tool results in the
session showed, which is what a layer sitting between an agent and its tools could know.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

WRITE_TOOLS = (
    "book_reservation",
    "cancel_reservation",
    "update_reservation_flights",
    "update_reservation_baggages",
    "update_reservation_passengers",
    "send_certificate",
)

# Tools whose result is the reservation as it stands after the write.
RESERVATION_RESULT_TOOLS = (
    "book_reservation",
    "cancel_reservation",
    "update_reservation_flights",
    "update_reservation_baggages",
    "update_reservation_passengers",
)


def _parse_json(text):
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


@dataclass
class Session:
    users: dict = field(default_factory=dict)
    reservations: dict = field(default_factory=dict)
    # (flight_number, date) -> the flight record from the latest search that listed it
    flights: dict = field(default_factory=dict)
    # (flight_number, date) -> status text from get_flight_status
    statuses: dict = field(default_factory=dict)

    def observe(self, name, arguments, content, error=False):
        """Record one tool result. Results of failed calls are ignored."""
        if error:
            return
        if name == "get_user_details":
            user = _parse_json(content)
            if isinstance(user, dict) and "user_id" in user:
                self.users[user["user_id"]] = user
        elif name == "get_reservation_details" or name in RESERVATION_RESULT_TOOLS:
            reservation = _parse_json(content)
            if isinstance(reservation, dict) and "reservation_id" in reservation:
                self.reservations[reservation["reservation_id"]] = reservation
        elif name == "search_direct_flight":
            result = _parse_json(content)
            if isinstance(result, list):
                searched_date = (arguments or {}).get("date")
                for flight in result:
                    self._remember_flight(flight, searched_date)
        elif name == "search_onestop_flight":
            result = _parse_json(content)
            if isinstance(result, list):
                searched_date = (arguments or {}).get("date")
                for itinerary in result:
                    if isinstance(itinerary, list):
                        for flight in itinerary:
                            self._remember_flight(flight, searched_date)
        elif name == "get_flight_status":
            args = arguments or {}
            if isinstance(content, str) and "flight_number" in args and "date" in args:
                self.statuses[(args["flight_number"], args["date"])] = content.strip()

    def _remember_flight(self, flight, searched_date):
        if not isinstance(flight, dict) or "flight_number" not in flight:
            return
        date = flight.get("date") or searched_date
        if date is None:
            return
        self.flights[(flight["flight_number"], date)] = flight
