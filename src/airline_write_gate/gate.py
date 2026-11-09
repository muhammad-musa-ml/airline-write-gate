"""The gate: look at one proposed write and say allow, hold or block.

block  a rule in the policy is broken by what the session has read.
hold   a rule applies but the session has not read what it needs to decide.
Every finding carries the repair an agent can act on.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import rules as rules_module
from .decision import Decision
from .evidence import WRITE_TOOLS, Session

POLICY_PATH = Path(__file__).with_name("policy.json")


def load_policy(path=None):
    text = Path(path or POLICY_PATH).read_text(encoding="utf-8")
    policy = json.loads(text, object_pairs_hook=_no_repeated_keys)
    if "now" not in policy or "rules" not in policy:
        raise ValueError("policy needs 'now' and 'rules'")
    datetime.fromisoformat(policy["now"])
    unknown = sorted(set(policy["rules"]) - set(rules_module.RULES))
    if unknown:
        raise ValueError("policy names rules that do not exist: " + ", ".join(unknown))
    return policy


def _no_repeated_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("repeated key in policy: " + key)
        out[key] = value
    return out


class Gate:
    def __init__(self, policy=None, session=None):
        self.policy = policy if policy is not None else load_policy()
        self.session = session if session is not None else Session()
        self.now = datetime.fromisoformat(self.policy["now"])

    def observe(self, name, arguments, content, error=False):
        self.session.observe(name, arguments, content, error)

    def evaluate(self, name, arguments):
        arguments = arguments or {}
        if name not in WRITE_TOOLS:
            return Decision(name, arguments, "allow", ())
        findings = []
        for rule_id, params in self.policy["rules"].items():
            if not params.get("enabled", True):
                continue
            rule = rules_module.RULES[rule_id]
            if name in rule.tools:
                findings.extend(rule.check(arguments, self.session, params, self.now, rule_id))
        if any(f.verdict == "block" for f in findings):
            verdict = "block"
        elif findings:
            verdict = "hold"
        else:
            verdict = "allow"
        return Decision(name, arguments, verdict, tuple(findings))
