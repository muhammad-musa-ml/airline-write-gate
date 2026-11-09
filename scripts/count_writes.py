"""Count the writes in the recorded files straight from the JSON and compare with results/results.json.

    python scripts/count_writes.py [DATA_DIR]

It does not import the gate, so a bug in replay or the evaluation cannot hide in both. Exit 1 on a mismatch.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRITES = {
    "book_reservation",
    "cancel_reservation",
    "update_reservation_flights",
    "update_reservation_baggages",
    "update_reservation_passengers",
    "send_certificate",
}


def count_writes(data):
    """Return (writes that ran, writes that raised, runs passed, runs failed)."""
    ran = errored = passed = failed = 0
    for sim in data["simulations"]:
        if sim["reward_info"]["reward"] == 1.0:
            passed += 1
        else:
            failed += 1
        names = {}
        for message in sim["messages"]:
            for call in message.get("tool_calls") or []:
                names[call["id"]] = call["name"]
            if message.get("role") == "tool" and names.get(message.get("id")) in WRITES:
                if message.get("error"):
                    errored += 1
                else:
                    ran += 1
    return ran, errored, passed, failed


def main(argv):
    data_dir = Path(argv[1] if len(argv) > 1 else ROOT / "data")
    committed = json.loads((ROOT / "results" / "results.json").read_text(encoding="utf-8"))["results"]
    paths = sorted(data_dir.glob("*_airline_*_4trials.json"))
    if not paths:
        print("no recorded files in", data_dir, file=sys.stderr)
        return 2
    bad = 0
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        agent = data["info"]["agent_info"]["llm"]
        got = count_writes(data)
        c = committed[agent]
        want = (c["writes_ran"], c["writes_errored"], c["passed"], c["failed"])
        status = "match" if got == want else "MISMATCH"
        bad += got != want
        print(f"{agent}: ran {got[0]}, raised {got[1]}, passed {got[2]}, failed {got[3]}: {status}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
