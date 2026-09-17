"""
Custom Exception Hierarchy — Domain-specific exceptions for clear error handling

Design Pattern: Exception Hierarchy Pattern
- All exceptions derive from a common base
- Enables catch-by-specificity (catch RuleError vs RoboSafeError)
- Carries structured context for logging
"""

from typing import Optional, Dict, Any


class RoboSafeError(Exception):
    """Base exception for all RoboSafe errors."""

    def __init__(self, message: str, context: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.context = context or {}

    def to_dict(self) -> dict:
        return {
            "error": self.__class__.__name__,
            "message": self.message,
            "context": self.context,
        }


# ─── Rule-related exceptions ───────────────────────────────
class RuleError(RoboSafeError):
    """Base exception for rule-related errors."""


class RuleAlreadyExistsError(RuleError):
    """Raised when trying to add a rule with a name that already exists."""

    def __init__(self, name: str):
        super().__init__(
            f"Rule '{name}' already exists",
            context={"rule_name": name},
        )


class RuleNotFoundError(RuleError):
    """Raised when a rule is not found."""

    def __init__(self, name: str):
        super().__init__(
            f"Rule '{name}' not found",
            context={"rule_name": name},
        )


class MaxRulesExceededError(RoboSafeError):
    """Raised when the maximum number of rules is exceeded."""

    def __init__(self, max_rules: int):
        super().__init__(
            f"Maximum {max_rules} rules allowed",
            context={"max_rules": max_rules},
        )


class InvalidExpressionError(RoboSafeError):
    """Raised when a rule expression is invalid or unsafe."""

    def __init__(self, expression: str, reason: str):
        super().__init__(
            f"Invalid expression: {reason}",
            context={"expression": expression[:200], "reason": reason},
        )


# ─── Action-related exceptions ─────────────────────────────
class InvalidActionError(RoboSafeError):
    """Raised when an action is invalid."""

    def __init__(self, field: str, reason: str):
        super().__init__(
            f"Invalid action field '{field}': {reason}",
            context={"field": field, "reason": reason},
        )


# ─── Monitor-related exceptions ────────────────────────────
class MonitorStateError(RoboSafeError):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, current_state: str, attempted_action: str):
        super().__init__(
            f"Cannot {attempted_action} in state '{current_state}'",
            context={"current_state": current_state, "attempted_action": attempted_action},
        )


class SensorDataError(RoboSafeError):
    """Raised when sensor data is invalid."""

    def __init__(self, reason: str):
        super().__init__(f"Invalid sensor data: {reason}")


# ─── Fallback-related exceptions ───────────────────────────
class FallbackStrategyError(RoboSafeError):
    """Raised when an unknown fallback strategy is requested."""

    def __init__(self, strategy: str, available: list):
        super().__init__(
            f"Unknown fallback strategy '{strategy}'. Available: {available}",
            context={"strategy": strategy, "available": available},
        )


class MaxRetriesExceededError(RoboSafeError):
    """Raised when the maximum retry count is exceeded."""

    def __init__(self, action_name: str, max_retries: int):
        super().__init__(
            f"Max retries ({max_retries}) exceeded for action '{action_name}'",
            context={"action_name": action_name, "max_retries": max_retries},
        )
