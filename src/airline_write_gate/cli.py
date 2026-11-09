"""Command line: replay one run, evaluate the four recorded files, or time the gate.

    airline-write-gate replay FILE --task 29 --trial 0
    airline-write-gate eval --data data [--write | --check]
    airline-write-gate bench --data data [--runs 5]

Exit codes: 0 fine, 1 a check failed (eval --check), 2 bad input.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from .evaluate import evaluate_files, markdown_table
from .gate import load_policy
from .replay import load_results, replay_simulation

ROOT = Path(__file__).resolve().parents[2]
RESULT_FILES = ("results.json", "results.md")


def _result_paths(data_dir):
    paths = sorted(Path(data_dir).glob("*_airline_*_4trials.json"))
    if not paths:
        raise SystemExit(f"no *_airline_*_4trials.json files in {data_dir}; run scripts/fetch_data.py first")
    return paths


def _splits(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def render(results, splits):
    body = json.dumps({"results": results, "splits": splits}, indent=2, sort_keys=True) + "\n"
    return body, markdown_table(results, splits) + "\n"


def cmd_replay(args):
    data = load_results(args.file)
    policy = load_policy(args.policy)
    for sim in data["simulations"]:
        if str(sim["task_id"]) == args.task and sim["trial"] == args.trial:
            print(f"task {args.task} trial {args.trial} reward {sim['reward_info']['reward']}")
            for rec in replay_simulation(sim, policy):
                ran = "errored" if rec.errored else "ran"
                print(f"  {rec.decision.verdict.upper():5} {rec.tool} ({ran}) {json.dumps(rec.arguments)[:100]}")
                for f in rec.decision.findings:
                    print(f"        {f.rule} [{f.verdict}]: {f.reason}")
                    print(f"        repair: {f.repair}")
            return 0
    print(f"no task {args.task} trial {args.trial} in {args.file}", file=sys.stderr)
    return 2


def cmd_eval(args):
    policy = load_policy(args.policy)
    data_dir = Path(args.data)
    paths = _result_paths(data_dir)
    later = data_dir / "tasks.json"
    if not later.exists():
        print(f"{later} is missing; run scripts/fetch_data.py", file=sys.stderr)
        return 2
    results = evaluate_files(paths, policy, later)
    splits = _splits(args.splits)
    missing = [a for names in splits.values() for a in names if a not in results]
    if missing:
        print("splits name models with no results file: " + ", ".join(missing), file=sys.stderr)
        return 2
    body, table = render(results, splits)
    out = Path(args.out)
    if args.write:
        out.mkdir(parents=True, exist_ok=True)
        (out / "results.json").write_text(body, encoding="utf-8", newline="\n")
        (out / "results.md").write_text(table, encoding="utf-8", newline="\n")
        print(table)
        return 0
    if args.check:
        stale = [
            name
            for name, text in zip(RESULT_FILES, (body, table))
            if not (out / name).exists() or (out / name).read_text(encoding="utf-8") != text
        ]
        if stale:
            print("committed results differ from what the code produces: " + ", ".join(stale), file=sys.stderr)
            return 1
        print("results match")
        return 0
    print(table)
    return 0


def cmd_bench(args):
    policy = load_policy(args.policy)
    paths = _result_paths(args.data)
    simulations = []
    for path in paths:
        simulations.extend(load_results(path)["simulations"])
    print(f"{len(simulations)} runs, {args.runs} passes; microseconds per Gate.evaluate call on a write")
    for number in range(1, args.runs + 1):
        times = []
        for sim in simulations:
            times.extend(r.nanoseconds for r in replay_simulation(sim, policy))
        times.sort()
        p50 = statistics.median(times) / 1000
        p99 = times[int(len(times) * 0.99) - 1] / 1000
        print(f"pass {number}: calls {len(times)}  p50 {p50:.1f}  p99 {p99:.1f}  max {times[-1] / 1000:.1f}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="airline-write-gate", description=__doc__.split("\n")[0])
    parser.add_argument("--policy", default=None, help="policy JSON (default: the one in the package)")
    sub = parser.add_subparsers(dest="command", required=True)

    replay = sub.add_parser("replay", help="print the gate's decision for every write in one recorded run")
    replay.add_argument("file")
    replay.add_argument("--task", required=True)
    replay.add_argument("--trial", type=int, required=True)
    replay.set_defaults(func=cmd_replay)

    evaluate = sub.add_parser("eval", help="evaluate every recorded file in --data")
    evaluate.add_argument("--data", default="data")
    evaluate.add_argument("--splits", default=str(ROOT / "splits.json"))
    evaluate.add_argument("--out", default=str(ROOT / "results"))
    group = evaluate.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help="write results/results.json and results.md")
    group.add_argument("--check", action="store_true", help="fail if the committed results differ")
    evaluate.set_defaults(func=cmd_eval)

    bench = sub.add_parser("bench", help="time Gate.evaluate over every write in the recorded files")
    bench.add_argument("--data", default="data")
    bench.add_argument("--runs", type=int, default=5)
    bench.set_defaults(func=cmd_bench)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
