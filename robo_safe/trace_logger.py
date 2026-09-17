"""
Trace Logger — Full-chain execution logging

Records the complete decision chain: AI brain output → safety validation
→ execution monitoring → fallback (if any) → final outcome.
"""

from typing import List, Optional
from .engine import ValidationResult, Action
import time
import json


class TraceLogger:
    """
    Logs the full execution trace for auditing and debugging.

    Each trace entry records:
    - The original action from the AI brain
    - Safety validation result (passed / blocked rules)
    - Execution snapshots (sensor readings over time)
    - Fallback actions (if triggered)
    - Final outcome
    """

    def __init__(self):
        self.entries: List[dict] = []

    def log_validation(self, result: ValidationResult):
        """Log a safety validation event."""
        entry = {
            "event": "validation",
            "timestamp": result.timestamp,
            "action": {
                "name": result.action.name,
                "params": result.action.params,
                "source": result.action.source,
                "priority": result.action.priority,
            },
            "status": result.status.value,
            "passed_rules": result.passed_rules,
            "failed_rules": result.failed_rules,
            "message": result.message,
            "fallback_action": {
                "name": result.fallback_action.name,
                "params": result.fallback_action.params,
                "source": result.fallback_action.source,
            } if result.fallback_action else None,
        }
        self.entries.append(entry)

    def log_execution(self, action: Action, snapshots: list):
        """Log an execution event with sensor trace."""
        entry = {
            "event": "execution",
            "timestamp": time.time(),
            "action": {
                "name": action.name,
                "params": action.params,
            },
            "snapshots": snapshots,
            "snapshot_count": len(snapshots),
            "had_anomaly": any(s.get("anomaly") for s in snapshots),
        }
        self.entries.append(entry)

    def log_fallback(self, trigger: str, fallback_action: Action, strategy: str):
        """Log a fallback event."""
        entry = {
            "event": "fallback",
            "timestamp": time.time(),
            "trigger": trigger,
            "strategy": strategy,
            "fallback_action": {
                "name": fallback_action.name,
                "params": fallback_action.params,
                "source": fallback_action.source,
            },
        }
        self.entries.append(entry)

    def log_outcome(self, action_name: str, outcome: str, detail: str = ""):
        """Log the final outcome of an action."""
        entry = {
            "event": "outcome",
            "timestamp": time.time(),
            "action": action_name,
            "outcome": outcome,
            "detail": detail,
        }
        self.entries.append(entry)

    def get_trace(self) -> list:
        """Return the full trace."""
        return self.entries

    def get_trace_json(self) -> str:
        """Return the full trace as JSON string."""
        return json.dumps(self.entries, indent=2, default=str)

    def summary(self) -> dict:
        """Return a summary of all logged events."""
        total = len(self.entries)
        validations = sum(1 for e in self.entries if e["event"] == "validation")
        blocked = sum(
            1 for e in self.entries
            if e["event"] == "validation" and e["status"] in ("blocked", "fallback")
        )
        fallbacks = sum(1 for e in self.entries if e["event"] == "fallback")
        executions = sum(1 for e in self.entries if e["event"] == "execution")
        anomalies = sum(
            1 for e in self.entries
            if e["event"] == "execution" and e.get("had_anomaly")
        )

        return {
            "total_events": total,
            "validations": validations,
            "blocked": blocked,
            "fallbacks": fallbacks,
            "executions": executions,
            "anomalies_detected": anomalies,
            "block_rate": f"{blocked/max(validations,1)*100:.1f}%",
        }

    def clear(self):
        """Clear all trace entries."""
        self.entries = []
