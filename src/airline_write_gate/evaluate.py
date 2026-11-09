"""Count what the gate says over recorded runs, with the benchmark's own outcomes beside it.

Two references for "the benchmark wanted this call":
  run gold    the expected actions of the task set the recorded runs were scored against,
              embedded in each results file;
  later gold  the expected actions in the repository's tasks.json at the pinned commit, which
              was revised after those runs were recorded.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict

from .evidence import WRITE_TOOLS
from .replay import load_results, replay_simulation


def write_actions(task):
    return [a for a in task["evaluation_criteria"].get("actions") or [] if a["name"] in WRITE_TOOLS]


def matches(action, tool, arguments):
    """The tau2 rule for an action check: same tool, and every argument the gold names equal."""
    return action["name"] == tool and all(arguments.get(k) == v for k, v in action["arguments"].items())


def in_gold(task, tool, arguments):
    return any(matches(a, tool, arguments) for a in write_actions(task))


def _canon(action):
    return (action["name"], json.dumps(action["arguments"], sort_keys=True))


def removed_actions(run_task, later_task):
    """Write actions the run gold expects that the later gold does not."""
    later = {_canon(a) for a in write_actions(later_task)}
    return [a for a in write_actions(run_task) if _canon(a) not in later]


def _verdict_counts(records):
    counts = Counter(r.decision.verdict for r in records)
    return {v: counts.get(v, 0) for v in ("allow", "hold", "block")}


def evaluate_results(data, policy, later_tasks=None):
    """Summarize one results file. later_tasks maps task id to the repository's current task."""
    run_tasks = {t["id"]: t for t in data["tasks"]}
    summary = {
        "agent": data["info"]["agent_info"]["llm"],
        "trajectories": len(data["simulations"]),
        "passed": 0,
        "failed": 0,
        "writes_ran": 0,
        "writes_errored": 0,
        "verdicts_ran": Counter(),
        "verdicts_errored": Counter(),
        "trajectories_with_block": {"passed": 0, "failed": 0},
        "trajectories_with_hold": {"passed": 0, "failed": 0},
        "blocks_by_rule": defaultdict(lambda: {"passed": 0, "failed": 0}),
        "holds_by_rule": defaultdict(lambda: {"passed": 0, "failed": 0}),
        "blocks_in_passed_runs": [],
        "nanoseconds": [],
    }
    removed = {}  # (task id, tool, canonical arguments) -> {"action", "verdicts": Counter}
    if later_tasks is not None:
        for task_id, task in run_tasks.items():
            for action in removed_actions(task, later_tasks[task_id]):
                removed[(task_id, *_canon(action))] = {"action": action, "verdicts": Counter(), "trials": 0}

    for sim in data["simulations"]:
        outcome = "passed" if sim["reward_info"]["reward"] == 1.0 else "failed"
        summary[outcome] += 1
        task_id = str(sim["task_id"])
        task = run_tasks[task_id]
        records = replay_simulation(sim, policy)
        ran = [r for r in records if not r.errored]
        errored = [r for r in records if r.errored]
        summary["writes_ran"] += len(ran)
        summary["writes_errored"] += len(errored)
        summary["verdicts_ran"].update(r.decision.verdict for r in ran)
        summary["verdicts_errored"].update(r.decision.verdict for r in errored)
        summary["nanoseconds"].extend(r.nanoseconds for r in records)
        if any(r.decision.verdict == "block" for r in ran):
            summary["trajectories_with_block"][outcome] += 1
        if any(r.decision.verdict == "hold" for r in ran):
            summary["trajectories_with_hold"][outcome] += 1
        for r in ran:
            # one count per rule per call, even when a rule has a finding for each of several segments
            for rule in {f.rule for f in r.decision.findings if f.verdict == "block"}:
                summary["blocks_by_rule"][rule][outcome] += 1
            for rule in {f.rule for f in r.decision.findings if f.verdict == "hold"}:
                summary["holds_by_rule"][rule][outcome] += 1
            if r.decision.verdict == "block" and outcome == "passed":
                entry = {
                    "task": task_id,
                    "trial": sim["trial"],
                    "tool": r.tool,
                    "rules": sorted({f.rule for f in r.decision.findings if f.verdict == "block"}),
                    "in_run_gold": in_gold(task, r.tool, r.arguments),
                }
                if later_tasks is not None:
                    entry["in_later_gold"] = in_gold(later_tasks[task_id], r.tool, r.arguments)
                summary["blocks_in_passed_runs"].append(entry)
            for key, item in removed.items():
                if key[0] == task_id and matches(item["action"], r.tool, r.arguments):
                    item["verdicts"][r.decision.verdict] += 1
    summary["removed_gold_actions"] = [
        {
            "task": key[0],
            "tool": key[1],
            "agent_calls": sum(item["verdicts"].values()),
            "verdicts": {v: item["verdicts"].get(v, 0) for v in ("allow", "hold", "block")},
        }
        for key, item in sorted(removed.items(), key=lambda kv: (int(kv[0][0]), kv[0][1], kv[0][2]))
    ]
    return summary


def to_plain(summary):
    """JSON-ready copy without the raw timings (those are not reproducible)."""
    plain = {k: v for k, v in summary.items() if k != "nanoseconds"}
    plain["verdicts_ran"] = {v: summary["verdicts_ran"].get(v, 0) for v in ("allow", "hold", "block")}
    plain["verdicts_errored"] = {v: summary["verdicts_errored"].get(v, 0) for v in ("allow", "hold", "block")}
    plain["blocks_by_rule"] = {k: dict(v) for k, v in sorted(summary["blocks_by_rule"].items())}
    plain["holds_by_rule"] = {k: dict(v) for k, v in sorted(summary["holds_by_rule"].items())}
    plain["blocks_in_passed_runs"] = sorted(
        summary["blocks_in_passed_runs"], key=lambda e: (int(e["task"]), e["trial"], e["tool"])
    )
    return plain


def load_tasks_by_id(path):
    with open(path, "r", encoding="utf-8") as handle:
        return {t["id"]: t for t in json.load(handle)}


def evaluate_files(paths, policy, later_tasks_path=None):
    later = load_tasks_by_id(later_tasks_path) if later_tasks_path else None
    out = {}
    for path in paths:
        summary = evaluate_results(load_results(path), policy, later)
        out[summary["agent"]] = to_plain(summary)
    return out


def markdown_table(results, splits):
    """The results as a table, grouped by split. results maps agent -> plain summary."""
    lines = []
    header = (
        "| split | model | runs (passed / failed) | writes that ran | allow | hold | block "
        "| runs with a block (passed / failed) | blocks in passed runs also in later gold |"
    )
    lines += [header, "|---|---|---|---|---|---|---|---|---|"]
    for split in ("development", "held_out"):
        for agent in splits[split]:
            r = results.get(agent)
            if r is None:
                continue
            v = r["verdicts_ran"]
            both = [e for e in r["blocks_in_passed_runs"] if e.get("in_later_gold")]
            lines.append(
                f"| {split.replace('_', '-')} | {agent} | {r['trajectories']} ({r['passed']} / {r['failed']}) "
                f"| {r['writes_ran']} | {v['allow']} | {v['hold']} | {v['block']} "
                f"| {r['trajectories_with_block']['passed']} / {r['trajectories_with_block']['failed']} "
                f"| {len(both)} of {len(r['blocks_in_passed_runs'])} |"
            )
    return "\n".join(lines)
