"""
Trace Logger — Full-chain execution logging

Records the complete decision chain: AI brain output -> safety validation
-> execution monitoring -> fallback (if any) -> final outcome.

重构要点：
- 使用 collections.deque(maxlen=...) 实现 O(1) 追加和自动裁剪
- 维护增量计数器，summary() 为 O(1)
- get_trace() 返回防御性拷贝
- 添加线程安全（threading.Lock）
- 添加日志记录
- clear() 同时重置计数器
"""

# 标准库
from collections import deque
from typing import List, Optional, TYPE_CHECKING
import json
import threading
import time

# 本地模块
from .logger import get_logger

if TYPE_CHECKING:
    # 仅用于类型提示，避免循环导入（engine.py 会在运行时导入本模块）
    from .engine import ValidationResult, Action


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

    def __init__(self, max_entries: int = 10000):
        # 使用 deque 实现固定容量、O(1) 追加和自动裁剪
        self.entries: deque = deque(maxlen=max_entries)
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._logger = get_logger("trace_logger")

        # 增量计数器 —— summary() 为 O(1)
        self._count_validation: int = 0
        self._count_blocked: int = 0
        self._count_fallback: int = 0
        self._count_execution: int = 0
        self._count_anomaly: int = 0

    # ─── 日志记录方法 ──────────────────────────────────────────

    def log_validation(self, result: "ValidationResult"):
        """Log a safety validation event."""
        if result is None:
            self._logger.warning("log_validation 收到 None 结果，跳过记录")
            return

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

        with self._lock:
            self.entries.append(entry)
            self._count_validation += 1
            if result.status.value in ("blocked", "fallback"):
                self._count_blocked += 1

        self._logger.debug("记录验证事件: action='%s' status=%s",
                          result.action.name, result.status.value)

    def log_execution(self, action: "Action", snapshots: list):
        """Log an execution event with sensor trace."""
        if action is None:
            self._logger.warning("log_execution 收到 None action，跳过记录")
            return

        had_anomaly = snapshots is not None and any(
            s.get("anomaly") for s in snapshots
        )

        entry = {
            "event": "execution",
            "timestamp": time.time(),
            "action": {
                "name": action.name,
                "params": action.params,
            },
            "snapshots": snapshots or [],
            "snapshot_count": len(snapshots) if snapshots else 0,
            "had_anomaly": had_anomaly,
        }

        with self._lock:
            self.entries.append(entry)
            self._count_execution += 1
            if had_anomaly:
                self._count_anomaly += 1

        self._logger.debug("记录执行事件: action='%s' snapshots=%d anomaly=%s",
                          action.name, len(snapshots) if snapshots else 0, had_anomaly)

    def log_fallback(self, trigger: str, fallback_action: "Action", strategy: str):
        """Log a fallback event."""
        if fallback_action is None:
            self._logger.warning("log_fallback 收到 None fallback_action，跳过记录")
            return

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

        with self._lock:
            self.entries.append(entry)
            self._count_fallback += 1

        self._logger.debug("记录降级事件: trigger='%s' strategy=%s", trigger, strategy)

    def log_outcome(self, action_name: str, outcome: str, detail: str = ""):
        """Log the final outcome of an action."""
        if action_name is None:
            self._logger.warning("log_outcome 收到 None action_name，跳过记录")
            return

        entry = {
            "event": "outcome",
            "timestamp": time.time(),
            "action": action_name,
            "outcome": outcome,
            "detail": detail,
        }

        with self._lock:
            self.entries.append(entry)

        self._logger.debug("记录结果事件: action='%s' outcome=%s", action_name, outcome)

    # ─── 查询方法 ─────────────────────────────────────────────

    def get_trace(self) -> list:
        """Return the full trace (defensive copy)."""
        with self._lock:
            return list(self.entries)

    def get_trace_json(self) -> str:
        """Return the full trace as JSON string."""
        with self._lock:
            entries = list(self.entries)
        return json.dumps(entries, indent=2, default=str)

    def summary(self) -> dict:
        """Return a summary of all logged events. O(1) via incremental counters."""
        with self._lock:
            total = len(self.entries)
            validations = self._count_validation
            blocked = self._count_blocked
            fallbacks = self._count_fallback
            executions = self._count_execution
            anomalies = self._count_anomaly

        return {
            "total_events": total,
            "validations": validations,
            "blocked": blocked,
            "fallbacks": fallbacks,
            "executions": executions,
            "anomalies_detected": anomalies,
            "block_rate": f"{blocked / max(validations, 1) * 100:.1f}%",
        }

    def clear(self):
        """Clear all trace entries and reset counters."""
        with self._lock:
            self.entries.clear()
            self._count_validation = 0
            self._count_blocked = 0
            self._count_fallback = 0
            self._count_execution = 0
            self._count_anomaly = 0
        self._logger.info("轨迹日志已清空")
