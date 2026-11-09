from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    rule: str
    verdict: str  # "block" or "hold"
    reason: str
    repair: str


@dataclass(frozen=True)
class Decision:
    tool: str
    arguments: dict
    verdict: str  # "allow", "hold" or "block"
    findings: tuple

    def to_agent(self):
        """What goes back to the agent instead of running the call."""
        return {
            "executed": self.verdict == "allow",
            "verdict": self.verdict,
            "findings": [{"rule": f.rule, "reason": f.reason, "repair": f.repair} for f in self.findings],
        }
