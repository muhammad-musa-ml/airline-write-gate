"""Run recorded tau2-bench trajectories through the gate in shadow mode.

Shadow mode: every call in the recording already ran. The gate sees each write before its result
exists, says what it would have done, and then observes the real result like any other read.
Nothing is changed or re-run, so this measures the gate, not an agent that listens to it.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from .decision import Decision
from .evidence import WRITE_TOOLS
from .gate import Gate


@dataclass(frozen=True)
class CallRecord:
    task_id: str
    trial: int
    tool: str
    arguments: dict
    decision: Decision
    errored: bool  # the recorded tool call raised, so it changed nothing
    nanoseconds: int  # time Gate.evaluate took for this call


def load_results(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def replay_simulation(simulation, policy):
    """Return one CallRecord per write call the agent under test made in this simulation."""
    gate = Gate(policy)
    pending = {}  # tool call id -> index into records
    calls = {}
    records = []
    for message in simulation["messages"]:
        for call in message.get("tool_calls") or []:
            if call.get("requestor", "assistant") != "assistant":
                continue
            calls[call["id"]] = call
            if call["name"] in WRITE_TOOLS:
                started = time.perf_counter_ns()
                decision = gate.evaluate(call["name"], call["arguments"])
                elapsed = time.perf_counter_ns() - started
                pending[call["id"]] = len(records)
                records.append(
                    CallRecord(
                        str(simulation["task_id"]),
                        simulation["trial"],
                        call["name"],
                        call["arguments"],
                        decision,
                        False,
                        elapsed,
                    )
                )
        if message.get("role") == "tool" and message.get("id") in calls:
            call = calls[message["id"]]
            errored = bool(message.get("error"))
            gate.observe(call["name"], call["arguments"], message.get("content"), errored)
            if message["id"] in pending:
                index = pending[message["id"]]
                old = records[index]
                records[index] = CallRecord(
                    old.task_id, old.trial, old.tool, old.arguments, old.decision, errored, old.nanoseconds
                )
    return records
