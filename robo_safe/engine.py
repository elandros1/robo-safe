"""
Safety Engine — Core validation logic

The engine sits between the AI brain and the robot's execution layer.
Every action from the brain must pass through validate() before execution.

重构要点：
- 使用 threading.RLock 保护规则的增删操作
- add_rule 检查重名和数量上限
- validate() 支持 short_circuit 短路评估
- 使用 logger 记录验证过程
- load_default_rules() 独立方法，with_defaults 参数控制
- 使用 config.SafetyThresholds 替换硬编码阈值
- ValidationResult.to_dict() 包含 action 的 timestamp
"""

# 标准库
from dataclasses import dataclass, field
from typing import Callable, Optional, List, Dict, Any
from enum import Enum
import threading
import time

# 本地模块
from .config import SafetyThresholds, DEFAULT_THRESHOLDS, DEFAULT_SERVER_CONFIG
from .exceptions import RuleAlreadyExistsError, MaxRulesExceededError
from .logger import get_logger
from .trace_logger import TraceLogger


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
                "timestamp": self.action.timestamp,  # 包含 action 的 timestamp
            },
            "passed_rules": self.passed_rules,
            "failed_rules": self.failed_rules,
            "message": self.message,
            "fallback_action": {
                "name": self.fallback_action.name,
                "params": self.fallback_action.params,
                "timestamp": self.fallback_action.timestamp,
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
        AI Brain -> Action -> SafetyEngine.validate() -> Approved / Blocked / Fallback -> Robot

    Args:
        thresholds: 安全阈值配置，默认使用 DEFAULT_THRESHOLDS
        with_defaults: 是否在初始化时加载默认规则，默认 True
    """

    def __init__(
        self,
        thresholds: SafetyThresholds = DEFAULT_THRESHOLDS,
        with_defaults: bool = True,
    ):
        self._thresholds = thresholds
        self._max_rules = DEFAULT_SERVER_CONFIG.max_rules
        self._lock = threading.RLock()
        self.rules: List[SafetyRule] = []
        self.trace_logger = TraceLogger(
            max_entries=DEFAULT_SERVER_CONFIG.trace_max_entries
        )
        self._logger = get_logger("engine")
        self._default_rules_loaded = False

        if with_defaults:
            self.load_default_rules()

    def add_rule(self, rule: SafetyRule):
        """Register a new safety rule.

        Raises:
            RuleAlreadyExistsError: 规则名已存在
            MaxRulesExceededError: 规则数量超过上限
        """
        with self._lock:
            # 检查重名
            if any(r.name == rule.name for r in self.rules):
                raise RuleAlreadyExistsError(rule.name)
            # 检查数量上限
            if len(self.rules) >= self._max_rules:
                raise MaxRulesExceededError(self._max_rules)
            self.rules.append(rule)
            self._logger.debug("已添加规则: %s (severity=%s)", rule.name, rule.severity)

    def remove_rule(self, name: str):
        """Remove a safety rule by name."""
        with self._lock:
            self.rules = [r for r in self.rules if r.name != name]
            self._logger.debug("已移除规则: %s", name)

    def list_rules(self) -> List[dict]:
        """List all registered rules."""
        with self._lock:
            return [
                {"name": r.name, "severity": r.severity, "message": r.message}
                for r in self.rules
            ]

    def validate(self, action: Action, short_circuit: bool = False) -> ValidationResult:
        """
        Validate an action against all safety rules.

        Args:
            action: The action to validate.
            short_circuit: If True, stop evaluation at the first blocking rule.

        Returns ValidationResult with status:
        - APPROVED: all rules passed
        - BLOCKED: at least one rule blocked the action
        - FALLBACK: a blocked action with a fallback action available
        """
        passed = []
        failed = []
        fallback_action = None

        # Track error messages per-rule without mutating rule objects
        rule_messages = {}

        # 在锁内获取规则快照，评估过程不持锁以避免长时间阻塞
        with self._lock:
            rules_snapshot = list(self.rules)

        for rule in rules_snapshot:
            try:
                is_safe = rule.check(action)
            except Exception as e:
                # 规则评估异常，视为不安全
                is_safe = False
                rule_messages[rule.name] = f"Rule '{rule.name}' check error: {str(e)}"
                self._logger.warning("规则 '%s' 评估异常: %s", rule.name, e)
            else:
                rule_messages[rule.name] = rule.message

            # DEBUG 级别记录每条规则评估结果
            self._logger.debug(
                "规则评估: %s -> %s (severity=%s)",
                rule.name, "PASS" if is_safe else "FAIL", rule.severity,
            )

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
                            params={
                                "reason": f"Blocked by rule '{rule.name}': {rule_messages[rule.name]}"
                            },
                            priority=2,
                            source="safety_engine",
                        )
                    # 短路评估：遇到第一个 block 规则即停止
                    if short_circuit:
                        self._logger.debug("短路评估：在规则 '%s' 处停止", rule.name)
                        break

        if failed:
            blocked_rules = [
                r for r in rules_snapshot
                if r.name in failed and r.severity == "block"
            ]
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

        # INFO 级别记录最终结果
        self._logger.info(
            "验证完成: action='%s' status=%s passed=%d failed=%d",
            action.name, status.value, len(passed), len(failed),
        )

        # Log the validation
        self.trace_logger.log_validation(result)

        return result

    def load_default_rules(self):
        """Load built-in default safety rules for humanoid robots.

        使用 config.SafetyThresholds 中的阈值，替换所有硬编码值。
        """
        t = self._thresholds

        # Rule 1: Speed limit
        self.add_rule(SafetyRule(
            name="speed_limit",
            check=lambda a: a.params.get("speed", 0) <= t.speed_limit,
            message=f"Speed exceeds {t.speed_limit} m/s — too fast for indoor operation",
            severity="block",
        ))

        # Rule 2: Force limit
        self.add_rule(SafetyRule(
            name="force_limit",
            check=lambda a: a.params.get("force", 0) <= t.force_limit,
            message=f"Force exceeds {t.force_limit}N — risk of damaging objects or humans",
            severity="block",
        ))

        # Rule 3: Joint angle limit (no self-collision)
        self.add_rule(SafetyRule(
            name="joint_angle_limit",
            check=lambda a: all(
                t.joint_angle_min <= angle <= t.joint_angle_max
                for angle in a.params.get("joint_angles", {}).values()
                if isinstance(angle, (int, float))
            ),
            message=f"Joint angle out of safe range ({t.joint_angle_min}° to {t.joint_angle_max}°)",
            severity="block",
        ))

        # Rule 4: Workspace boundary
        self.add_rule(SafetyRule(
            name="workspace_boundary",
            check=lambda a: all(
                t.workspace_min <= coord <= t.workspace_max
                for coord in (a.params.get("target") or [])
                if isinstance(coord, (int, float))
            ),
            message=f"Target position outside workspace boundary ({t.workspace_min} to {t.workspace_max} m)",
            severity="block",
        ))

        # Rule 5: High-temperature object detection
        self.add_rule(SafetyRule(
            name="thermal_safety",
            check=lambda a: a.params.get("object_temp", 25) <= t.thermal_limit,
            message=f"Object temperature exceeds {t.thermal_limit}°C — risk of thermal injury",
            severity="block",
        ))

        # Rule 6: Human proximity check
        self.add_rule(SafetyRule(
            name="human_proximity",
            check=lambda a: a.params.get("human_distance", 999) >= t.human_proximity_min,
            message=f"Human within {t.human_proximity_min}m — collision risk, action aborted",
            severity="block",
        ))

        # Rule 7: Emergency stop priority (always allow emergency stops)
        self.add_rule(SafetyRule(
            name="emergency_stop_allowed",
            check=lambda a: not (a.name == "emergency_stop" and a.priority < 2),
            message="Emergency stop must have priority 2",
            severity="warn",
        ))

        self._default_rules_loaded = True
        self._logger.info("已加载 %d 条默认安全规则", len(self.rules))
