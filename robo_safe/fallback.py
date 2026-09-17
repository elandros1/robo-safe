"""
Fallback Controller — Safe mode switching

When an action is blocked or execution is aborted, the fallback controller
determines the safest alternative action and triggers a graceful recovery.
"""

from dataclasses import dataclass
from typing import Optional, List, Callable
from .engine import Action, ExecutionStatus
import time


@dataclass
class FallbackPlan:
    """A recovery plan for when an action fails safety checks."""
    trigger: str                        # what triggered the fallback
    fallback_action: Action             # the safe alternative action
    recovery_strategy: str = "safe_stop"  # safe_stop / retry / degrade
    max_retries: int = 0                # how many times to retry
    retry_count: int = 0


class FallbackController:
    """
    Manages fallback actions when safety checks fail.

    Strategies:
    - safe_stop: immediately stop all motion
    - graceful_retreat: slowly move to a safe home position
    - degrade: reduce speed/force and retry
    - retry: retry the original action (if transient issue)
    """

    def __init__(self):
        self.fallback_history: List[FallbackPlan] = []
        self._strategies = {
            "safe_stop": self._safe_stop,
            "graceful_retreat": self._graceful_retreat,
            "degrade": self._degrade,
            "retry": self._retry,
        }

    def execute_fallback(self, action: Action, trigger: str, strategy: str = "safe_stop") -> FallbackPlan:
        """
        Execute a fallback strategy for a blocked/aborted action.
        """
        if strategy not in self._strategies:
            strategy = "safe_stop"

        fallback_action = self._strategies[strategy](action, trigger)

        plan = FallbackPlan(
            trigger=trigger,
            fallback_action=fallback_action,
            recovery_strategy=strategy,
        )
        self.fallback_history.append(plan)
        return plan

    def _safe_stop(self, action: Action, trigger: str) -> Action:
        """Immediately stop all motion."""
        return Action(
            name="safe_stop",
            params={"reason": trigger, "brake": True},
            priority=2,
            source="fallback_controller"
        )

    def _graceful_retreat(self, action: Action, trigger: str) -> Action:
        """Slowly move to a predefined safe home position."""
        return Action(
            name="move_to_home",
            params={
                "target": [0.0, 0.0, 0.5],
                "speed": 0.3,
                "force": 10.0,
                "reason": trigger,
            },
            priority=1,
            source="fallback_controller"
        )

    def _degrade(self, action: Action, trigger: str) -> Action:
        """Reduce speed and force, then retry."""
        degraded = Action(
            name=action.name,
            params={**action.params},
            priority=action.priority,
            source="fallback_controller"
        )
        # Reduce speed and force to 50%
        degraded.params["speed"] = min(action.params.get("speed", 1.0) * 0.5, 0.5)
        degraded.params["force"] = min(action.params.get("force", 20.0) * 0.5, 25.0)
        degraded.params["degraded"] = True
        degraded.params["degrade_reason"] = trigger
        return degraded

    def _retry(self, action: Action, trigger: str) -> Action:
        """Retry the same action (for transient issues)."""
        retried = Action(
            name=action.name,
            params={**action.params},
            priority=action.priority,
            source="fallback_controller"
        )
        retried.params["retry"] = True
        return retried

    def get_history(self) -> list:
        """Return fallback history as serializable data."""
        return [
            {
                "trigger": p.trigger,
                "fallback_action": {
                    "name": p.fallback_action.name,
                    "params": p.fallback_action.params,
                },
                "strategy": p.recovery_strategy,
                "timestamp": p.fallback_action.timestamp,
            }
            for p in self.fallback_history
        ]
