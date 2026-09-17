"""
Runtime Monitor — Real-time execution monitoring

After an action is approved, the monitor tracks the robot's execution
and can abort if safety conditions are violated mid-execution.

重构要点：
- 使用 config.SafetyThresholds 替换硬编码阈值
- __init__ 接受 thresholds 参数（默认 DEFAULT_THRESHOLDS）
- 所有 None 判断改为 is not None（修复 temperature=0 被跳过的 bug）
- 使用 threading.Lock 保护状态转换
- update() 中验证 sensor_data 是 dict 类型
- start() 中验证 action 不为 None
- 状态转换添加日志记录
- abort_callback 改名为 on_abort_callback
"""

# 标准库
from dataclasses import dataclass
from typing import Optional, Callable, List, Dict, TYPE_CHECKING
from enum import Enum
import threading
import time

# 本地模块
from .config import SafetyThresholds, DEFAULT_THRESHOLDS
from .exceptions import MonitorStateError, SensorDataError, InvalidActionError
from .logger import get_logger

if TYPE_CHECKING:
    from .engine import SafetyEngine, Action


class MonitorState(Enum):
    """Runtime monitor states."""
    IDLE = "idle"
    RUNNING = "running"
    ABORTED = "aborted"
    COMPLETED = "completed"


@dataclass
class ExecutionSnapshot:
    """A single sensor reading during action execution."""
    timestamp: float
    joint_positions: Dict[str, float]
    force_reading: float
    speed: float
    temperature: Optional[float] = None
    human_distance: Optional[float] = None
    anomaly: Optional[str] = None


class RuntimeMonitor:
    """
    Monitors action execution in real-time.

    Usage:
        monitor = RuntimeMonitor(engine)
        monitor.start(action)
        while monitor.state == MonitorState.RUNNING:
            monitor.update(sensor_data)

    Args:
        engine: 引用 SafetyEngine 实例（用于后续扩展）
        thresholds: 安全阈值配置，默认使用 DEFAULT_THRESHOLDS
    """

    def __init__(self, engine, thresholds: SafetyThresholds = DEFAULT_THRESHOLDS):
        self.engine = engine
        self._thresholds = thresholds
        self.state = MonitorState.IDLE
        self.current_action: Optional["Action"] = None
        self.snapshots: List[ExecutionSnapshot] = []
        self.on_abort_callback: Optional[Callable] = None
        self._monitor_thread = None
        self._running = False
        self._lock = threading.Lock()
        self._logger = get_logger("monitor")

    def start(self, action: "Action"):
        """Begin monitoring an action execution.

        Raises:
            InvalidActionError: action 为 None
            MonitorStateError: 当前状态非 IDLE，无法启动
        """
        if action is None:
            raise InvalidActionError("action", "action cannot be None")

        with self._lock:
            if self.state != MonitorState.IDLE:
                raise MonitorStateError(self.state.value, "start")
            self.current_action = action
            self.state = MonitorState.RUNNING
            self.snapshots = []
            self._running = True

        self._logger.info("开始监控动作: %s", action.name)

    def update(self, sensor_data: dict) -> bool:
        """
        Feed real-time sensor data to the monitor.
        Returns True if execution should continue, False if aborted.

        Raises:
            SensorDataError: sensor_data 不是 dict 类型
            MonitorStateError: 当前状态非 RUNNING
        """
        if not isinstance(sensor_data, dict):
            raise SensorDataError("sensor_data must be a dict")

        with self._lock:
            if self.state != MonitorState.RUNNING:
                self._logger.warning("在非 RUNNING 状态下调用 update: %s", self.state.value)
                return False

            snapshot = ExecutionSnapshot(
                timestamp=time.time(),
                joint_positions=sensor_data.get("joint_positions", {}),
                force_reading=sensor_data.get("force", 0),
                speed=sensor_data.get("speed", 0),
                temperature=sensor_data.get("temperature"),
                human_distance=sensor_data.get("human_distance"),
            )

            # Check for anomalies —— 使用 is not None 替代 falsy 检查
            anomalies = []
            t = self._thresholds

            # Force spike
            if snapshot.force_reading > t.rt_force_spike:
                anomalies.append(
                    f"Force spike: {snapshot.force_reading:.1f}N exceeds {t.rt_force_spike}N"
                )

            # Speed overrun
            if snapshot.speed > t.rt_speed_overrun:
                anomalies.append(
                    f"Speed overrun: {snapshot.speed:.2f}m/s exceeds {t.rt_speed_overrun}m/s"
                )

            # Temperature critical —— 修复 temperature=0 被 falsy 跳过的 bug
            if snapshot.temperature is not None and snapshot.temperature > t.rt_temp_critical:
                anomalies.append(
                    f"Critical temperature: {snapshot.temperature}°C exceeds {t.rt_temp_critical}°C"
                )

            # Human too close during motion —— 修复 human_distance=0 被 falsy 跳过的 bug
            if snapshot.human_distance is not None and snapshot.human_distance < t.rt_human_too_close:
                anomalies.append(
                    f"Human too close: {snapshot.human_distance:.2f}m < {t.rt_human_too_close}m"
                )

            if anomalies:
                snapshot.anomaly = "; ".join(anomalies)
                self.state = MonitorState.ABORTED
                self._running = False
                self.snapshots.append(snapshot)
                self._logger.warning(
                    "检测到异常，中止执行: %s", snapshot.anomaly
                )
                # 在锁外调用回调，避免持锁调用外部代码导致死锁
                callback = self.on_abort_callback
            else:
                self.snapshots.append(snapshot)
                return True

        # 锁外执行回调
        if callback:
            callback(snapshot)
        return False

    def complete(self):
        """Mark the current action as completed successfully."""
        with self._lock:
            if self.state == MonitorState.RUNNING:
                self.state = MonitorState.COMPLETED
                self._running = False
                self._logger.info("动作执行完成")
            else:
                self._logger.warning(
                    "在非 RUNNING 状态下调用 complete: %s", self.state.value
                )

    def abort(self, reason: str = "Manual abort"):
        """Force abort the current execution."""
        with self._lock:
            prev_state = self.state
            self.state = MonitorState.ABORTED
            self._running = False
            if self.snapshots:
                self.snapshots[-1].anomaly = reason
            self._logger.warning(
                "强制中止执行 (prev_state=%s): %s", prev_state.value, reason
            )

    def get_trace(self) -> list:
        """Return the full execution trace as serializable data."""
        with self._lock:
            return [
                {
                    "timestamp": s.timestamp,
                    "force": s.force_reading,
                    "speed": s.speed,
                    "temperature": s.temperature,
                    "human_distance": s.human_distance,
                    "anomaly": s.anomaly,
                }
                for s in self.snapshots
            ]
