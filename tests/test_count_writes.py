import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location("count_writes", ROOT / "scripts" / "count_writes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sim(reward, calls):
    messages = []
    for i, (name, error) in enumerate(calls):
        messages.append({"role": "assistant", "tool_calls": [{"id": f"c{i}", "name": name, "arguments": {}}]})
        messages.append({"role": "tool", "id": f"c{i}", "content": "x", "error": error})
    return {"reward_info": {"reward": reward}, "messages": messages}


def test_only_write_tools_count_and_errors_are_kept_apart():
    data = {
        "simulations": [
            sim(1.0, [("get_user_details", False), ("cancel_reservation", False), ("book_reservation", True)]),
            sim(0.0, [("send_certificate", False), ("search_direct_flight", False)]),
        ]
    }
    assert load().count_writes(data) == (2, 1, 1, 1)


def test_the_fixture_has_the_counts_the_evaluation_reports():
    data = json.loads((ROOT / "tests" / "fixtures" / "runs.json").read_text(encoding="utf-8"))
    assert load().count_writes(data) == (5, 0, 3, 0)
