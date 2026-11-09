"""The rules. Each one is a sentence of the airline policy or a fact the policy depends on.

policy.md says, twice, that the API does not check these for the agent. The rule list is
mine, written from that text; it is not Sierra's and it is not a complete reading of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .decision import Finding
from .evidence import Session


@dataclass(frozen=True)
class Rule:
    tools: tuple
    check: object  # (name, arguments, session, params, now, rule_id) -> list of Finding
    required: tuple = ()  # parameters the policy must give for this rule


def _finding(rule_id, verdict, reason, repair):
    return Finding(rule_id, verdict, reason, repair)


# ---- read before write ----

RESERVATION_WRITES = (
    "cancel_reservation",
    "update_reservation_flights",
    "update_reservation_baggages",
    "update_reservation_passengers",
)


def read_before_write(name, arguments, session: Session, params, now, rule_id):
    """A write that depends on a record the session never read cannot be checked at all."""
    if "reservation_id" in arguments:
        rid = arguments["reservation_id"]
        if rid not in session.reservations:
            return [
                _finding(
                    rule_id,
                    "hold",
                    f"reservation {rid} has not been read in this session",
                    f"call get_reservation_details(reservation_id={rid!r}) and check the policy against what it returns",
                )
            ]
        return []
    uid = arguments.get("user_id")
    if uid is not None and uid not in session.users:
        return [
            _finding(
                rule_id,
                "hold",
                f"user {uid} has not been read in this session",
                f"call get_user_details(user_id={uid!r}) first",
            )
        ]
    return []


# ---- cancellation ----

FLOWN_STATUSES = ("flying", "landed")


def flight_status(session: Session, number, date):
    """The status the session has seen for a flight on a date: a status call first, then a search result."""
    status = session.statuses.get((number, date))
    if status is None:
        status = (session.flights.get((number, date)) or {}).get("status")
    return status


def flown(flight, session: Session, now):
    """True, False, or None when the session cannot tell (a flight dated today with no status read)."""
    status = flight_status(session, flight["flight_number"], flight["date"])
    if status is not None:
        return status in FLOWN_STATUSES
    day = datetime.fromisoformat(flight["date"]).date()
    if day < now.date():
        return True
    if day == now.date():
        return None
    return False


def cancel_eligibility(name, arguments, session: Session, params, now, rule_id):
    res = session.reservations.get(arguments.get("reservation_id"))
    if res is None:
        return []  # read_before_write holds this call
    rid = res["reservation_id"]
    findings = []
    unknown_today = []
    for flight in res["flights"]:
        state = flown(flight, session, now)
        if state is True:
            findings.append(
                _finding(
                    rule_id,
                    "block",
                    f"{flight['flight_number']} on {flight['date']} has already been flown",
                    "do not cancel; policy says a reservation with a flown segment is transferred to a human agent",
                )
            )
        elif state is None:
            unknown_today.append(flight)
    if findings:
        return findings
    if unknown_today:
        names = ", ".join(f"{f['flight_number']} on {f['date']}" for f in unknown_today)
        return [
            _finding(
                rule_id,
                "hold",
                f"{names} departs today and its status has not been read",
                "call get_flight_status for each of these flights to learn whether it has been flown",
            )
        ]

    window = timedelta(hours=params.get("window_hours", 24))
    created = datetime.fromisoformat(res["created_at"])
    if now - window <= created <= now:
        return []
    if res.get("cabin") == "business":
        return []
    if res.get("insurance") == "yes":
        return []  # the stated reason (health or weather) is not in the call, so it is not checked
    statuses = [flight_status(session, f["flight_number"], f["date"]) for f in res["flights"]]
    if any(s == "cancelled" for s in statuses):
        return []
    if any(s is None for s in statuses):
        missing = [f for f, s in zip(res["flights"], statuses) if s is None]
        names = ", ".join(f"{f['flight_number']} on {f['date']}" for f in missing)
        return [
            _finding(
                rule_id,
                "hold",
                f"{rid} was booked {created.isoformat()}, is {res['cabin']} with insurance {res['insurance']}, "
                f"and the status of {names} has not been read",
                "call get_flight_status for each flight; the airline cancelling a flight is the one condition left that could allow this",
            )
        ]
    return [
        _finding(
            rule_id,
            "block",
            f"{rid} was booked {created.isoformat()} (more than {params.get('window_hours', 24)} hours before "
            f"{now.isoformat()}), is {res['cabin']}, has insurance {res['insurance']}, and no flight on it is cancelled",
            "do not cancel; none of the four conditions in the cancellation policy holds",
        )
    ]


# ---- changing flights ----


def _keys(flights):
    return sorted((f["flight_number"], f["date"]) for f in flights)


def _label(key):
    return f"{key[0]} on {key[1]}"


def basic_economy_flights(name, arguments, session: Session, params, now, rule_id):
    """Basic economy flights cannot be modified. A cabin-only change keeps the same flights."""
    res = session.reservations.get(arguments.get("reservation_id"))
    if res is None or res.get("cabin") != "basic_economy":
        return []
    if _keys(arguments.get("flights", [])) == _keys(res["flights"]):
        return []
    return [
        _finding(
            rule_id,
            "block",
            f"{res['reservation_id']} is basic economy and the call changes its flights",
            "do not change the flights; only the cabin can change on a basic economy reservation",
        )
    ]


def cabin_change_after_flown(name, arguments, session: Session, params, now, rule_id):
    res = session.reservations.get(arguments.get("reservation_id"))
    if res is None or arguments.get("cabin") in (None, res.get("cabin")):
        return []
    findings = []
    for flight in res["flights"]:
        state = flown(flight, session, now)
        if state is True:
            findings.append(
                _finding(
                    rule_id,
                    "block",
                    f"the cabin cannot change because {_label((flight['flight_number'], flight['date']))} has been flown",
                    "do not change the cabin; policy allows it only while no flight on the reservation has been flown",
                )
            )
        elif state is None:
            findings.append(
                _finding(
                    rule_id,
                    "hold",
                    f"{_label((flight['flight_number'], flight['date']))} departs today and its status has not been read",
                    "call get_flight_status for it to learn whether it has been flown",
                )
            )
    return findings


def _flight_record(res, session: Session, key):
    for flight in res["flights"]:
        if (flight["flight_number"], flight["date"]) == key:
            return flight
    return session.flights.get(key)


def trip_endpoints(name, arguments, session: Session, params, now, rule_id):
    """A modified reservation keeps its origin, destination and trip type."""
    res = session.reservations.get(arguments.get("reservation_id"))
    flights = arguments.get("flights") or []
    if res is None or not flights or _keys(flights) == _keys(res["flights"]):
        return []
    records = [_flight_record(res, session, (f["flight_number"], f["date"])) for f in flights]
    if any(r is None for r in records):
        return []  # flights-seen-available holds the call
    first_origin, last_destination = records[0]["origin"], records[-1]["destination"]
    if res["flight_type"] == "one_way":
        ok = first_origin == res["origin"] and last_destination == res["destination"]
    else:
        ok = (
            first_origin == res["origin"]
            and last_destination == res["origin"]
            and any(r["destination"] == res["destination"] for r in records)
        )
    if ok:
        return []
    return [
        _finding(
            rule_id,
            "block",
            f"{res['reservation_id']} is {res['flight_type']} {res['origin']} to {res['destination']}, "
            f"and the new flights run {first_origin} to {last_destination}",
            "keep the origin, destination and trip type; search for flights on the same route",
        )
    ]


def flights_seen_available(name, arguments, session: Session, params, now, rule_id):
    """A flight the call books must have been listed by a search, available, with seats in the cabin."""
    flights = arguments.get("flights") or []
    if name == "book_reservation":
        cabin = arguments.get("cabin")
        seats_needed = len(arguments.get("passengers") or [])
        kept = set()
    else:
        res = session.reservations.get(arguments.get("reservation_id"))
        if res is None:
            return []
        cabin = arguments.get("cabin") or res.get("cabin")
        seats_needed = len(res["passengers"])
        kept = {(f["flight_number"], f["date"]) for f in res["flights"]} if cabin == res.get("cabin") else set()
    unseen, closed, short = [], [], []
    for flight in flights:
        key = (flight["flight_number"], flight["date"])
        if key in kept:
            continue
        record = session.flights.get(key)
        if record is None:
            unseen.append(key)
        elif record.get("status") != "available":
            closed.append(f"{_label(key)} is {record.get('status')}")
        elif (record.get("available_seats") or {}).get(cabin, 0) < seats_needed:
            short.append(f"{_label(key)} has {(record.get('available_seats') or {}).get(cabin, 0)} {cabin} seats for {seats_needed} passengers")
    findings = []
    if closed or short:
        findings.append(
            _finding(
                rule_id,
                "block",
                "; ".join(closed + short),
                "search again and pick a flight that is available with enough seats in the cabin",
            )
        )
    if unseen:
        findings.append(
            _finding(
                rule_id,
                "hold",
                "no search in this session listed " + ", ".join(_label(k) for k in unseen),
                "call search_direct_flight or search_onestop_flight for each date and use only flights it returns",
            )
        )
    return findings


# ---- baggage ----


def baggage_no_removal(name, arguments, session: Session, params, now, rule_id):
    res = session.reservations.get(arguments.get("reservation_id"))
    if res is None or "total_baggages" not in arguments:
        return []
    if arguments["total_baggages"] < res["total_baggages"]:
        return [
            _finding(
                rule_id,
                "block",
                f"the call lowers checked bags on {res['reservation_id']} from {res['total_baggages']} to {arguments['total_baggages']}",
                "do not remove checked bags; policy lets a user add them but not remove them",
            )
        ]
    return []


def free_allowance(user, cabin, params):
    return params["free_bags"][user["membership"]][cabin]


def baggage_allowance(name, arguments, session: Session, params, now, rule_id):
    """The non-free bag count must be the total minus the free allowance for the member level and cabin."""
    if "total_baggages" not in arguments or "nonfree_baggages" not in arguments:
        return []
    if name == "book_reservation":
        uid, cabin, passengers = arguments.get("user_id"), arguments.get("cabin"), len(arguments.get("passengers") or [])
    else:
        res = session.reservations.get(arguments.get("reservation_id"))
        if res is None:
            return []
        uid, cabin, passengers = res["user_id"], res["cabin"], len(res["passengers"])
    user = session.users.get(uid)
    if user is None:
        return [
            _finding(
                rule_id,
                "hold",
                f"user {uid} has not been read, so the free bag allowance is unknown",
                f"call get_user_details(user_id={uid!r}) to learn the membership level",
            )
        ]
    free = free_allowance(user, cabin, params) * passengers
    expected = max(0, arguments["total_baggages"] - free)
    if arguments["nonfree_baggages"] == expected:
        return []
    return [
        _finding(
            rule_id,
            "block",
            f"{arguments['total_baggages']} bags for {passengers} {cabin} passenger(s) of a {user['membership']} member "
            f"leave {expected} to pay for, and the call says {arguments['nonfree_baggages']}",
            f"set nonfree_baggages to {expected}",
        )
    ]


# ---- booking ----


def booking_limits(name, arguments, session: Session, params, now, rule_id):
    findings = []
    passengers = len(arguments.get("passengers") or [])
    if passengers > params["max_passengers"]:
        findings.append(
            _finding(
                rule_id,
                "block",
                f"the booking has {passengers} passengers and a reservation holds at most {params['max_passengers']}",
                f"split the booking so that no reservation has more than {params['max_passengers']} passengers",
            )
        )
    counts = {}
    for payment in arguments.get("payment_methods") or []:
        kind = payment["payment_id"].rsplit("_", 1)[0]
        counts[kind] = counts.get(kind, 0) + 1
    for kind, limit in params["max_payment_methods"].items():
        if counts.get(kind, 0) > limit:
            findings.append(
                _finding(
                    rule_id,
                    "block",
                    f"the booking pays with {counts[kind]} {kind} methods and the limit is {limit}",
                    f"use at most {limit} {kind} method(s) on one reservation",
                )
            )
    return findings


# ---- compensation ----


def certificate_eligibility(name, arguments, session: Session, params, now, rule_id):
    """A certificate needs an eligible reservation and a confirmed cancelled or delayed flight with the right amount."""
    uid, amount = arguments.get("user_id"), arguments.get("amount")
    user = session.users.get(uid)
    if user is None:
        return []  # read_before_write holds this call
    owned = [r for r in session.reservations.values() if r["user_id"] == uid]
    if not owned:
        return [
            _finding(
                rule_id,
                "hold",
                f"no reservation of {uid} has been read, so the compensation has no flight to be about",
                "call get_reservation_details for the reservation the user complains about",
            )
        ]
    member = user.get("membership") in ("silver", "gold")
    eligible = [r for r in owned if member or r.get("insurance") == "yes" or r.get("cabin") == "business"]
    if not eligible:
        return [
            _finding(
                rule_id,
                "block",
                f"{uid} is a {user.get('membership')} member and no reservation read has insurance or a business cabin",
                "do not compensate; policy allows it only for silver or gold members, travel insurance, or business class",
            )
        ]
    unread = False
    unchanged = []
    for res in eligible:
        passengers = len(res["passengers"])
        statuses = [flight_status(session, f["flight_number"], f["date"]) for f in res["flights"]]
        unread = unread or any(s is None for s in statuses)
        if amount == params["cancelled_per_passenger"] * passengers and "cancelled" in statuses:
            return []
        if amount == params["delayed_per_passenger"] * passengers and "delayed" in statuses:
            if res["reservation_id"] in session.changed:
                return []
            unchanged.append(res["reservation_id"])
    if unchanged:
        return [
            _finding(
                rule_id,
                "block",
                f"amount {amount} fits a delayed flight on {', '.join(unchanged)}, which the agent has not changed or cancelled",
                "do not send the certificate yet; policy pays for a delayed flight only after the reservation is changed or cancelled",
            )
        ]
    if unread:
        return [
            _finding(
                rule_id,
                "hold",
                f"amount {amount} is not yet backed by a flight status the session has read",
                "call get_flight_status for the flights on the reservation to confirm they were cancelled or delayed",
            )
        ]
    return [
        _finding(
            rule_id,
            "block",
            f"amount {amount} is not {params['cancelled_per_passenger']} per passenger with a cancelled flight "
            f"or {params['delayed_per_passenger']} per passenger with a delayed flight on any eligible reservation read",
            "do not send the certificate; confirm the facts first and use the amount the policy sets",
        )
    ]


RULES = {
    "read-before-write": Rule(
        tools=RESERVATION_WRITES + ("book_reservation", "send_certificate"),
        check=read_before_write,
    ),
    "cancel-eligibility": Rule(tools=("cancel_reservation",), check=cancel_eligibility),
    "flight-change-basic-economy": Rule(tools=("update_reservation_flights",), check=basic_economy_flights),
    "cabin-change-after-flown": Rule(tools=("update_reservation_flights",), check=cabin_change_after_flown),
    "trip-endpoints": Rule(tools=("update_reservation_flights",), check=trip_endpoints),
    "flights-seen-available": Rule(tools=("update_reservation_flights", "book_reservation"), check=flights_seen_available),
    "baggage-no-removal": Rule(tools=("update_reservation_baggages",), check=baggage_no_removal),
    "baggage-allowance": Rule(
        tools=("update_reservation_baggages", "book_reservation"),
        check=baggage_allowance,
        required=("free_bags",),
    ),
    "booking-limits": Rule(
        tools=("book_reservation",),
        check=booking_limits,
        required=("max_passengers", "max_payment_methods"),
    ),
    "certificate-eligibility": Rule(
        tools=("send_certificate",),
        check=certificate_eligibility,
        required=("cancelled_per_passenger", "delayed_per_passenger"),
    ),
}
