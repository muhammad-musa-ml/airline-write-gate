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
    check: object  # (arguments, session, params, now, rule_id) -> list of Finding


def _finding(rule_id, verdict, reason, repair):
    return Finding(rule_id, verdict, reason, repair)


# ---- read before write ----

RESERVATION_WRITES = (
    "cancel_reservation",
    "update_reservation_flights",
    "update_reservation_baggages",
    "update_reservation_passengers",
)


def read_before_write(arguments, session: Session, params, now, rule_id):
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


def flown(flight, session: Session, now):
    """True, False, or None when the session cannot tell (a flight dated today with no status read)."""
    status = session.statuses.get((flight["flight_number"], flight["date"]))
    if status is not None:
        return status in FLOWN_STATUSES
    day = datetime.fromisoformat(flight["date"]).date()
    if day < now.date():
        return True
    if day == now.date():
        return None
    return False


def cancel_eligibility(arguments, session: Session, params, now, rule_id):
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
    statuses = [session.statuses.get((f["flight_number"], f["date"])) for f in res["flights"]]
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


RULES = {
    "read-before-write": Rule(
        tools=RESERVATION_WRITES + ("book_reservation", "send_certificate"),
        check=read_before_write,
    ),
    "cancel-eligibility": Rule(tools=("cancel_reservation",), check=cancel_eligibility),
}
