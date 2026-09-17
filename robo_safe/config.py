"""
Configuration Module — Centralized configuration for RoboSafe

Design Pattern: Configuration Object Pattern
- All hardcoded thresholds and limits are centralized here
- Enables easy testing with custom configurations
- Supports environment variable overrides
"""

import os
from dataclasses import dataclass, field
from typing import Dict, Any


@dataclass(frozen=True)
class SafetyThresholds:
    """Safety threshold values for rule validation."""
    # Pre-execution validation thresholds
    speed_limit: float = 2.0
    force_limit: float = 50.0
    joint_angle_min: float = -180.0
    joint_angle_max: float = 180.0
    workspace_min: float = -1.0
    workspace_max: float = 2.0
    thermal_limit: float = 60.0
    human_proximity_min: float = 0.3

    # Runtime monitor thresholds (typically stricter than pre-execution)
    rt_force_spike: float = 80.0
    rt_speed_overrun: float = 2.5
    rt_temp_critical: float = 70.0
    rt_human_too_close: float = 0.2

    def to_dict(self) -> Dict[str, float]:
        return {
            "speed_limit": self.speed_limit,
            "force_limit": self.force_limit,
            "joint_angle_min": self.joint_angle_min,
            "joint_angle_max": self.joint_angle_max,
            "workspace_min": self.workspace_min,
            "workspace_max": self.workspace_max,
            "thermal_limit": self.thermal_limit,
            "human_proximity_min": self.human_proximity_min,
            "rt_force_spike": self.rt_force_spike,
            "rt_speed_overrun": self.rt_speed_overrun,
            "rt_temp_critical": self.rt_temp_critical,
            "rt_human_too_close": self.rt_human_too_close,
        }


@dataclass(frozen=True)
class ServerConfig:
    """Server runtime configuration."""
    api_key: str = field(
        default_factory=lambda: os.environ.get("ROBO_SAFE_API_KEY", "robo-safe-dev-key")
    )
    cors_origin: str = field(
        default_factory=lambda: os.environ.get("ROBO_SAFE_CORS_ORIGIN", "http://localhost:3001")
    )
    max_body_size: int = 1024 * 100  # 100KB
    max_name_length: int = 200
    max_params_size: int = 50
    max_rules: int = 100
    rate_limit: int = 30  # requests per window
    rate_window: int = 60  # seconds
    rate_map_max_size: int = 10000
    trace_max_entries: int = 10000
    fallback_history_max: int = 1000


# Default singleton instance
DEFAULT_THRESHOLDS = SafetyThresholds()
DEFAULT_SERVER_CONFIG = ServerConfig()
