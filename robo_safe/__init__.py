"""
RoboSafe — Embodied AI Safety Harness
=====================================

A lightweight safety verification layer for humanoid robot AI brains.

Intercepts every action the AI brain outputs, validates it against safety rules,
monitors execution in real-time, and auto-falls-back to safe mode on danger.

Usage:
    from robo_safe import SafetyEngine, Action, SafetyRule

    engine = SafetyEngine()
    engine.add_rule(SafetyRule(
        name="speed_limit",
        check=lambda action: action.params.get("speed", 0) <= 2.0,
        message="Speed exceeds 2.0 m/s — too fast for indoor operation"
    ))

    action = Action(name="move_arm", params={"speed": 3.5, "target": [0.5, 0.2, 0.8]})
    result = engine.validate(action)
    # result.blocked == True, result.reason == "Speed exceeds 2.0 m/s..."
"""

from .engine import SafetyEngine, Action, SafetyRule, ValidationResult, ExecutionStatus
from .monitor import RuntimeMonitor
from .fallback import FallbackController
from .trace_logger import TraceLogger
from .server import create_app

__version__ = "1.0.0"
__all__ = [
    "SafetyEngine",
    "Action",
    "SafetyRule",
    "ValidationResult",
    "ExecutionStatus",
    "RuntimeMonitor",
    "FallbackController",
    "TraceLogger",
    "create_app",
]
