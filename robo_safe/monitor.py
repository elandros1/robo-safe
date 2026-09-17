"""
Runtime Monitor — Real-time execution monitoring

After an action is approved, the monitor tracks the robot's execution
and can abort if safety conditions are violated mid-execution.
"""

from dataclasses import dataclass, field
from typing import Optional, Callable, List, Dict
from enum import Enum
import time
import threading


class MonitorState(Enum):
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
    """

    def __init__(self, engine):
        self.engine = engine
        self.state = MonitorState.IDLE
        self.current_action = None
        self.snapshots: List[ExecutionSnapshot] = []
        self.abort_callback: Optional[Callable] = None
        self._monitor_thread = None
        self._running = False

    def start(self, action):
        """Begin monitoring an action execution."""
        self.current_action = action
        self.state = MonitorState.RUNNING
        self.snapshots = []
        self._running = True

    def update(self, sensor_data: dict) -> bool:
        """
        Feed real-time sensor data to the monitor.
        Returns True if execution should continue, False if aborted.
        """
        if self.state != MonitorState.RUNNING:
            return False

        snapshot = ExecutionSnapshot(
            timestamp=time.time(),
            joint_positions=sensor_data.get("joint_positions", {}),
            force_reading=sensor_data.get("force", 0),
            speed=sensor_data.get("speed", 0),
            temperature=sensor_data.get("temperature"),
            human_distance=sensor_data.get("human_distance"),
        )

        # Check for anomalies
        anomalies = []

        # Force spike
        if snapshot.force_reading > 80:
            anomalies.append(f"Force spike: {snapshot.force_reading:.1f}N exceeds 80N")

        # Speed overrun
        if snapshot.speed > 2.5:
            anomalies.append(f"Speed overrun: {snapshot.speed:.2f}m/s exceeds 2.5m/s")

        # Temperature critical
        if snapshot.temperature and snapshot.temperature > 70:
            anomalies.append(f"Critical temperature: {snapshot.temperature}°C exceeds 70°C")

        # Human too close during motion
        if snapshot.human_distance and snapshot.human_distance < 0.2:
            anomalies.append(f"Human too close: {snapshot.human_distance:.2f}m < 0.2m")

        if anomalies:
            snapshot.anomaly = "; ".join(anomalies)
            self.state = MonitorState.ABORTED
            self._running = False
            self.snapshots.append(snapshot)
            if self.abort_callback:
                self.abort_callback(snapshot)
            return False

        self.snapshots.append(snapshot)
        return True

    def complete(self):
        """Mark the current action as completed successfully."""
        if self.state == MonitorState.RUNNING:
            self.state = MonitorState.COMPLETED
            self._running = False

    def abort(self, reason: str = "Manual abort"):
        """Force abort the current execution."""
        self.state = MonitorState.ABORTED
        self._running = False
        if self.snapshots:
            self.snapshots[-1].anomaly = reason

    def get_trace(self) -> list:
        """Return the full execution trace as serializable data."""
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
