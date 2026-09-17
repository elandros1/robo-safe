"""
Fallback Controller — Safe mode switching

When an action is blocked or execution is aborted, the fallback controller
determines the safest alternative action and triggers a graceful recovery.

重构要点：
- 使用 config.ServerConfig.fallback_history_max 限制历史大小
- _degrade() 中使用 dict copy 创建新 params（不可变操作）
- retry 计数改为维护 dict[str, int] 计数器，O(1) 查找
- 使用 FallbackStrategyError 替换默认 fallback 策略
- 使用 MaxRetriesExceededError 用于 max_retries 场景
- 添加日志记录
- get_history() 返回防御性拷贝
"""

# 标准库
from dataclasses import dataclass
from typing import Optional, List, Dict
import threading

# 本地模块
from .config import DEFAULT_SERVER_CONFIG
from .engine import Action
from .exceptions import FallbackStrategyError, MaxRetriesExceededError
from .logger import get_logger


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

    Args:
        engine: 引用 SafetyEngine 实例，用于降级后重新验证
    """

    # 默认最大重试次数
    _DEFAULT_MAX_RETRIES = 3

    def __init__(self, engine=None):
        self.fallback_history: List[FallbackPlan] = []
        self._engine = engine
        self._strategies: Dict[str, callable] = {
            "safe_stop": self._safe_stop,
            "graceful_retreat": self._graceful_retreat,
            "degrade": self._degrade,
            "retry": self._retry,
        }
        # O(1) 重试计数器：action_name -> count
        self._retry_counts: Dict[str, int] = {}
        self._history_max = DEFAULT_SERVER_CONFIG.fallback_history_max
        self._lock = threading.Lock()
        self._logger = get_logger("fallback")

    def set_engine(self, engine):
        """Set the safety engine reference for re-validation of degraded actions."""
        self._engine = engine
        self._logger.debug("已设置 engine 引用")

    def execute_fallback(
        self, action: Action, trigger: str, strategy: str = "safe_stop"
    ) -> FallbackPlan:
        """
        Execute a fallback strategy for a blocked/aborted action.
        For 'degrade' and 'retry' strategies, the fallback action is
        re-validated through the safety engine if available.

        Raises:
            FallbackStrategyError: 未知策略
            MaxRetriesExceededError: 重试次数超过上限
        """
        # 检查策略是否有效 —— 不再静默回退到 safe_stop
        if strategy not in self._strategies:
            raise FallbackStrategyError(strategy, list(self._strategies.keys()))

        fallback_action = self._strategies[strategy](action, trigger)

        # For degrade strategy, re-validate the degraded action
        if strategy == "degrade" and self._engine:
            degraded_result = self._engine.validate(fallback_action)
            if degraded_result.blocked:
                # If degraded action still fails, escalate to safe_stop
                fallback_action = self._safe_stop(
                    action, trigger + " (degraded action still unsafe)"
                )
                strategy = "safe_stop"
                self._logger.warning(
                    "降级动作仍不安全，升级为 safe_stop: %s", action.name
                )

        # For retry strategy, enforce max_retries limit —— O(1) 查找
        if strategy == "retry":
            count = self._retry_counts.get(action.name, 0)
            if count >= self._DEFAULT_MAX_RETRIES:
                raise MaxRetriesExceededError(action.name, self._DEFAULT_MAX_RETRIES)
            self._retry_counts[action.name] = count + 1
            self._logger.info(
                "重试动作 '%s' (第 %d/%d 次)",
                action.name, count + 1, self._DEFAULT_MAX_RETRIES,
            )

        plan = FallbackPlan(
            trigger=trigger,
            fallback_action=fallback_action,
            recovery_strategy=strategy,
        )

        with self._lock:
            self.fallback_history.append(plan)
            # 限制历史大小，防止无界增长
            if len(self.fallback_history) > self._history_max:
                self.fallback_history = self.fallback_history[-self._history_max:]

        self._logger.info(
            "执行降级策略: action='%s' strategy=%s trigger='%s'",
            action.name, strategy, trigger,
        )
        return plan

    def _safe_stop(self, action: Action, trigger: str) -> Action:
        """Immediately stop all motion."""
        return Action(
            name="safe_stop",
            params={"reason": trigger, "brake": True},
            priority=2,
            source="fallback_controller",
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
            source="fallback_controller",
        )

    def _degrade(self, action: Action, trigger: str) -> Action:
        """Reduce speed and force, then retry.

        使用 dict copy 创建新 params，不修改原始 action 的 params。
        """
        # 创建 params 的副本，不修改原始 action 的 params（不可变操作）
        new_params = dict(action.params)
        new_params["speed"] = min(action.params.get("speed", 1.0) * 0.5, 0.5)
        new_params["force"] = min(action.params.get("force", 20.0) * 0.5, 25.0)
        new_params["degraded"] = True
        new_params["degrade_reason"] = trigger

        return Action(
            name=action.name,
            params=new_params,
            priority=action.priority,
            source="fallback_controller",
        )

    def _retry(self, action: Action, trigger: str) -> Action:
        """Retry the same action (for transient issues).

        使用 dict copy 创建新 params，不修改原始 action 的 params。
        """
        new_params = dict(action.params)
        new_params["retry"] = True

        return Action(
            name=action.name,
            params=new_params,
            priority=action.priority,
            source="fallback_controller",
        )

    def get_history(self) -> list:
        """Return fallback history as serializable data (defensive copy)."""
        with self._lock:
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
