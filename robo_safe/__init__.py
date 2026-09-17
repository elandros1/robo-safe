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

# 本地模块 —— 核心组件
from .engine import (
    SafetyEngine,
    Action,
    SafetyRule,
    ValidationResult,
    ExecutionStatus,
)
from .monitor import RuntimeMonitor, MonitorState, ExecutionSnapshot
from .fallback import FallbackController, FallbackPlan
from .trace_logger import TraceLogger
from .safe_eval import safe_eval, SafeEvalError
from .server import create_app

# 本地模块 —— 基础设施
from .config import (
    SafetyThresholds,
    ServerConfig,
    DEFAULT_THRESHOLDS,
    DEFAULT_SERVER_CONFIG,
)
from .exceptions import (
    RoboSafeError,
    RuleError,
    RuleAlreadyExistsError,
    RuleNotFoundError,
    MaxRulesExceededError,
    InvalidExpressionError,
    InvalidActionError,
    MonitorStateError,
    SensorDataError,
    FallbackStrategyError,
    MaxRetriesExceededError,
)
from .logger import get_logger

__version__ = "2.0.0"

__all__ = [
    # 核心组件
    "SafetyEngine",
    "Action",
    "SafetyRule",
    "ValidationResult",
    "ExecutionStatus",
    "RuntimeMonitor",
    "MonitorState",
    "ExecutionSnapshot",
    "FallbackController",
    "FallbackPlan",
    "TraceLogger",
    "safe_eval",
    "SafeEvalError",
    "create_app",
    # 配置
    "SafetyThresholds",
    "ServerConfig",
    "DEFAULT_THRESHOLDS",
    "DEFAULT_SERVER_CONFIG",
    # 异常
    "RoboSafeError",
    "RuleError",
    "RuleAlreadyExistsError",
    "RuleNotFoundError",
    "MaxRulesExceededError",
    "InvalidExpressionError",
    "InvalidActionError",
    "MonitorStateError",
    "SensorDataError",
    "FallbackStrategyError",
    "MaxRetriesExceededError",
    # 日志
    "get_logger",
]
