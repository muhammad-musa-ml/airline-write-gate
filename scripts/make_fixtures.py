"""Cut a small fixture out of the recorded files, so the tests run without the 36 MB download.

    python scripts/make_fixtures.py [DATA_DIR]

It keeps tool calls and tool results of three runs and drops the chat text, and keeps the two task
entries (run gold and later gold) those runs need. The runs are named in RUNS below.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from airline_write_gate.gate import load_policy  # noqa: E402
from airline_write_gate.replay import replay_simulation  # noqa: E402

GPT41 = "gpt-4.1-2025-04-14_airline_default_gpt-4.1-2025-04-14_4trials.json"
# (task id, trial, why it is here)
RUNS = [
    ("29", 0, "passed run; the call that changes the destination is blocked"),
    ("37", 1, "passed run; the cancellation of a reservation with flown segments is blocked"),
]


def trim(sim):
    messages = []
    for m in sim["messages"]:
        if m.get("tool_calls"):
            messages.append({"role": m["role"], "tool_calls": m["tool_calls"]})
        elif m["role"] == "tool":
            messages.append({k: m.get(k) for k in ("role", "id", "content", "error", "requestor")})
    return {
        "task_id": sim["task_id"],
        "trial": sim["trial"],
        "reward_info": {"reward": sim["reward_info"]["reward"]},
        "messages": messages,
    }


def all_allow_run(data, policy):
    best = None
    for sim in data["simulations"]:
        if sim["reward_info"]["reward"] != 1.0:
            continue
        records = replay_simulation(sim, policy)
        if records and all(r.decision.verdict == "allow" and not r.errored for r in records):
            if best is None or len(sim["messages"]) < len(best["messages"]):
                best = sim
    return best


def main(argv):
    data_dir = Path(argv[1] if len(argv) > 1 else ROOT / "data")
    policy = load_policy()
    data = json.loads((data_dir / GPT41).read_text(encoding="utf-8"))
    later = {t["id"]: t for t in json.loads((data_dir / "tasks.json").read_text(encoding="utf-8"))}
    picked = []
    for task_id, trial, _why in RUNS:
        picked.append(next(s for s in data["simulations"] if str(s["task_id"]) == task_id and s["trial"] == trial))
    clean = all_allow_run(data, policy)
    picked.append(clean)
    ids = sorted({str(s["task_id"]) for s in picked})
    run_tasks = {t["id"]: t for t in data["tasks"]}
    fixture = {
        "info": {"agent_info": {"llm": "fixture-from-" + data["info"]["agent_info"]["llm"]}},
        "tasks": [run_tasks[i] for i in ids],
        "simulations": [trim(s) for s in picked],
    }
    out = ROOT / "tests" / "fixtures"
    out.mkdir(exist_ok=True)
    (out / "runs.json").write_text(json.dumps(fixture, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    (out / "later_tasks.json").write_text(
        json.dumps([later[i] for i in ids], indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print("runs:", [(s["task_id"], s["trial"]) for s in picked])
    print("bytes:", (out / "runs.json").stat().st_size, (out / "later_tasks.json").stat().st_size)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
