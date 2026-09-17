"""
Safety Engine — Core validation logic

The engine sits between the AI brain and the robot's execution layer.
Every action from the brain must pass through validate() before execution.
"""

from dataclasses import dataclass, field
from typing import Callable, Optional, List, Dict, Any
from enum import Enum
import time
import json


class ExecutionStatus(Enum):
    """Status of an action after safety validation."""
    APPROVED = "approved"       # Safety checks passed, ready to execute
    BLOCKED = "blocked"         # Safety check failed, action denied
    FALLBACK = "fallback"        # Safety check failed, switching to safe mode
    MONITORING = "monitoring"   # Executing, under real-time monitoring
    ABORTED = "aborted"         # Aborted during execution due to safety violation
    COMPLETED = "completed"     # Finished successfully
    RECOVERED = "recovered"     # Recovered from a fallback


@dataclass
class Action:
    """An action produced by the AI brain, to be validated before execution."""
    name: str                           # e.g. "move_arm", "grasp", "walk_forward"
    params: Dict[str, Any] = field(default_factory=dict)  # action parameters
    priority: int = 0                   # 0=normal, 1=urgent, 2=emergency
    source: str = "llm"                 # who issued this action (llm / planner / manual)
    timestamp: float = field(default_factory=time.time)


@dataclass
class ValidationResult:
    """Result of safety validation on an action."""
    status: ExecutionStatus
    action: Action
    passed_rules: List[str] = field(default_factory=list)
    failed_rules: List[str] = field(default_factory=list)
    message: str = ""
    fallback_action: Optional[Action] = None
    timestamp: float = field(default_factory=time.time)

    @property
    def blocked(self) -> bool:
        return self.status in (ExecutionStatus.BLOCKED, ExecutionStatus.FALLBACK)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "action": {
                "name": self.action.name,
                "params": self.action.params,
                "source": self.action.source,
            },
            "passed_rules": self.passed_rules,
            "failed_rules": self.failed_rules,
            "message": self.message,
            "fallback_action": {
                "name": self.fallback_action.name,
                "params": self.fallback_action.params,
            } if self.fallback_action else None,
            "timestamp": self.timestamp,
        }


@dataclass
class SafetyRule:
    """
    A single safety rule. The check function receives an Action and returns
    True if the action is safe, False if it violates the rule.
    """
    name: str                           # unique rule name, e.g. "speed_limit"
    check: Callable[[Action], bool]     # returns True = safe, False = violation
    message: str                        # explanation when violated
    severity: str = "block"             # "block" (deny) or "warn" (allow with warning)
    fallback: Optional[Callable[[], Action]] = None  # optional fallback action generator


class SafetyEngine:
    """
    The core safety engine. Intercepts AI brain actions and validates them
    against all registered safety rules before execution.

    Flow:
        AI Brain → Action → SafetyEngine.validate() → Approved / Blocked / Fallback → Robot
    """

    def __init__(self):
        self.rules: List[SafetyRule] = []
        from .trace_logger import TraceLogger
        self.trace_logger = TraceLogger()
        self._default_rules_loaded = False
        self._load_default_rules()

    def add_rule(self, rule: SafetyRule):
        """Register a new safety rule."""
        self.rules.append(rule)

    def remove_rule(self, name: str):
        """Remove a safety rule by name."""
        self.rules = [r for r in self.rules if r.name != name]

    def list_rules(self) -> List[dict]:
        """List all registered rules."""
        return [{"name": r.name, "severity": r.severity, "message": r.message} for r in self.rules]

    def validate(self, action: Action) -> ValidationResult:
        """
        Validate an action against all safety rules.

        Returns ValidationResult with status:
        - APPROVED: all rules passed
        - BLOCKED: at least one rule blocked the action
        - FALLBACK: a blocked action with a fallback action available
        """
        passed = []
        failed = []
        fallback_action = None

        for rule in self.rules:
            try:
                is_safe = rule.check(action)
            except Exception as e:
                # If the check itself errors, treat as blocked for safety
                is_safe = False
                rule.message = f"Rule '{rule.name}' check error: {str(e)}"

            if is_safe:
                passed.append(rule.name)
            else:
                failed.append(rule.name)
                if rule.severity == "block":
                    if rule.fallback:
                        fallback_action = rule.fallback()
                    else:
                        fallback_action = Action(
                            name="safe_stop",
                            params={"reason": f"Blocked by rule '{rule.name}': {rule.message}"},
                            priority=2,
                            source="safety_engine"
                        )

        if failed:
            blocked_rules = [r for r in self.rules if r.name in failed and r.severity == "block"]
            warn_rules = [r for r in self.rules if r.name in failed and r.severity == "warn"]

            if blocked_rules:
                status = ExecutionStatus.FALLBACK if fallback_action else ExecutionStatus.BLOCKED
                msg = f"Blocked by: {', '.join(failed)}"
            else:
                # Only warnings, no hard blocks — allow with warning
                status = ExecutionStatus.APPROVED
                msg = f"Warnings: {', '.join(failed)}"
        else:
            status = ExecutionStatus.APPROVED
            msg = "All safety checks passed"

        result = ValidationResult(
            status=status,
            action=action,
            passed_rules=passed,
            failed_rules=failed,
            message=msg,
            fallback_action=fallback_action,
        )

        # Log the validation
        self.trace_logger.log_validation(result)

        return result

    def _load_default_rules(self):
        """Load built-in default safety rules for humanoid robots."""

        # Rule 1: Speed limit
        self.add_rule(SafetyRule(
            name="speed_limit",
            check=lambda a: a.params.get("speed", 0) <= 2.0,
            message="Speed exceeds 2.0 m/s — too fast for indoor operation",
            severity="block"
        ))

        # Rule 2: Force limit
        self.add_rule(SafetyRule(
            name="force_limit",
            check=lambda a: a.params.get("force", 0) <= 50.0,
            message="Force exceeds 50N — risk of damaging objects or humans",
            severity="block"
        ))

        # Rule 3: Joint angle limit (no self-collision)
        self.add_rule(SafetyRule(
            name="joint_angle_limit",
            check=lambda a: all(
                -180 <= angle <= 180
                for angle in a.params.get("joint_angles", {}).values()
                if isinstance(angle, (int, float))
            ),
            message="Joint angle out of safe range (-180° to 180°)",
            severity="block"
        ))

        # Rule 4: Workspace boundary
        self.add_rule(SafetyRule(
            name="workspace_boundary",
            check=lambda a: all(
                -1.0 <= coord <= 2.0
                for coord in (a.params.get("target") or [])
                if isinstance(coord, (int, float))
            ),
            message="Target position outside workspace boundary (-1.0 to 2.0 m)",
            severity="block"
        ))

        # Rule 5: High-temperature object detection
        self.add_rule(SafetyRule(
            name="thermal_safety",
            check=lambda a: a.params.get("object_temp", 25) <= 60,
            message="Object temperature exceeds 60°C — risk of thermal injury",
            severity="block"
        ))

        # Rule 6: Human proximity check
        self.add_rule(SafetyRule(
            name="human_proximity",
            check=lambda a: a.params.get("human_distance", 999) >= 0.3,
            message="Human within 0.3m — collision risk, action aborted",
            severity="block"
        ))

        # Rule 7: Emergency stop priority (always allow emergency stops)
        self.add_rule(SafetyRule(
            name="emergency_stop_allowed",
            check=lambda a: not (a.name == "emergency_stop" and a.priority < 2),
            message="Emergency stop must have priority 2",
            severity="warn"
        ))

        self._default_rules_loaded = True
