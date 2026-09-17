# 🛡️ RoboSafe — Embodied AI Safety Harness

> A lightweight safety verification layer for humanoid robot AI brains. Intercepts every action the AI brain outputs, validates it against safety rules, monitors execution in real-time, and auto-falls-back to safe mode on danger.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://python.org/)
[![Status](https://img.shields.io/badge/Status-MVP%20Ready-green.svg)](#)

---

## ⚡ Quick Start (30 seconds)

**Option 1: Clone and start the web dashboard** (zero dependencies, pure Python stdlib)

```bash
git clone https://github.com/elandros1/robo-safe.git
cd robo-safe && python3 -m robo_safe.server
```

Open `http://localhost:3001` in your browser to access the safety validation dashboard.

**Option 2: Use in your code** (3 lines of Python to intercept dangerous actions)

```python
from robo_safe import SafetyEngine, Action

engine = SafetyEngine()                          # Load 7 built-in safety rules
result = engine.validate(Action("move_arm", {"speed": 3.5}))  # Speed exceeded -> auto-blocked
print(result.blocked, result.fallback_action.name)  # True "safe_stop"
```

> Zero third-party dependencies. Python 3.8+ ready out of the box.

---

## 📖 Overview

As humanoid robots (Figure AI, Tesla Optimus, Unitree, Boston Dynamics) become more autonomous, their AI brains (LLM/VLA models) can output actions that are **dangerous** — too fast, too much force, targeting a human, or grabbing a hot object.

**RoboSafe sits between the AI brain and the robot's execution layer**:

```
AI Brain (LLM/VLA)  →  RoboSafe  →  Robot Executor
                        │
                        ├── Pre-execution validation (rule engine)
                        ├── Real-time monitoring (sensor-based)
                        ├── Automatic fallback (safe stop / retreat / degrade)
                        └── Full-chain trace logging
```

### Why?

RoboBench evaluation (2026) shows that **execution-level fault diagnosis scores only 10-30%** across current embodied AI systems — it is the single biggest bottleneck in commercial humanoid robot deployment. No open-source project currently provides an independent, pluggable safety layer.

---

## 🚀 More Examples

### Python SDK — Full validation flow

```python
from robo_safe import SafetyEngine, Action

# Create safety engine with built-in rules
engine = SafetyEngine()

# An action from the AI brain
action = Action(name="move_arm", params={"speed": 3.5, "force": 20, "target": [0.5, 0.2, 0.8]})

# Validate before execution
result = engine.validate(action)

print(result.status)    # ExecutionStatus.BLOCKED
print(result.blocked)   # True
print(result.message)   # "Blocked by: speed_limit"
print(result.fallback_action.name)  # "safe_stop"
```

### Web Dashboard — Execution mode with monitoring

```bash
# Start from source (zero dependencies)
git clone https://github.com/elandros1/robo-safe.git
cd robo-safe && python3 -m robo_safe.server
# Open http://localhost:3001 in your browser
```

---

## 📡 REST API

### Validate an Action

```http
POST /api/validate
Content-Type: application/json

{
  "name": "grasp_object",
  "params": {"speed": 3.5, "force": 20, "target": [0.5, 0.2, 0.8]},
  "source": "llm"
}
```

**Response:**
```json
{
  "status": "blocked",
  "action": { "name": "grasp_object", "params": { "speed": 3.5, ... } },
  "passed_rules": ["force_limit", "joint_angle_limit", ...],
  "failed_rules": ["speed_limit"],
  "message": "Blocked by: speed_limit",
  "fallback_action": {
    "name": "safe_stop",
    "params": { "reason": "Speed exceeds 2.0 m/s..." }
  }
}
```

### Execute with Monitoring

```http
POST /api/execute
Content-Type: application/json

{
  "name": "move_arm",
  "params": {"speed": 1.0, "force": 15},
  "sensor_data": {"force": 12, "speed": 1.1, "temperature": 25, "human_distance": 1.5}
}
```

### List Safety Rules

```http
GET /api/rules
```

### Get Execution Trace

```http
GET /api/trace
```

### Add Custom Rule

```http
POST /api/rules
Content-Type: application/json

{
  "name": "weight_limit",
  "expression": "params.get('weight', 0) < 5",
  "message": "Object weight exceeds 5kg",
  "severity": "block"
}
```

---

## 🏗 Architecture

```
robo-safe/
├── robo_safe/
│   ├── __init__.py          # Public API
│   ├── engine.py            # Core safety engine + rule validation
│   ├── monitor.py           # Real-time execution monitor
│   ├── fallback.py          # Fallback controller (safe stop / retreat / degrade)
│   ├── trace_logger.py      # Full-chain execution trace logging
│   └── server.py            # HTTP server (REST API + web dashboard)
├── public/
│   └── index.html           # Web dashboard (live demo)
├── examples/
├── tests/
├── setup.py                 # PyPI package config
├── LICENSE                  # MIT
└── README.md
```

---

## 🛡️ Built-in Safety Rules

| Rule | Check | Severity |
|------|-------|----------|
| `speed_limit` | Speed ≤ 2.0 m/s | block |
| `force_limit` | Force ≤ 50N | block |
| `joint_angle_limit` | Joint angles within -180° to 180° | block |
| `workspace_boundary` | Target position within -1.0 to 2.0 m | block |
| `thermal_safety` | Object temperature ≤ 60°C | block |
| `human_proximity` | Human distance ≥ 0.3m | block |
| `emergency_stop_allowed` | Emergency stop requires priority 2 | warn |

### Fallback Strategies

| Strategy | Description |
|----------|-------------|
| `safe_stop` | Immediately stop all motion |
| `graceful_retreat` | Slowly move to safe home position (0.3 m/s) |
| `degrade` | Reduce speed/force to 50% and retry |
| `retry` | Retry the original action (transient issues) |

---

## 🔧 ROS2 Integration (Optional)

RoboSafe is framework-agnostic. For ROS2 integration:

```python
# Bridge: AI brain → RoboSafe → ROS2 action server
from robo_safe import SafetyEngine, Action

engine = SafetyEngine()

def on_brain_action(msg):
    action = Action(
        name=msg.action_type,
        params={"speed": msg.speed, "force": msg.force, "target": msg.target}
    )
    result = engine.validate(action)
    if not result.blocked:
        # Publish to ROS2 execution topic
        execute_action_pub.publish(result.action)
    else:
        # Publish fallback
        execute_action_pub.publish(result.fallback_action)
```

---

## 💡 Use Cases

- **Humanoid robot companies**: Add a safety layer to your LLM/VLA brain
- **Robotics researchers**: Benchmark safety performance of different AI brains
- **ROS2 developers**: Pluggable safety node for any robot system
- **Insurance & compliance**: Audit trail for robot safety certification
- **Education**: Teach embodied AI safety concepts with the visual dashboard

---

## 📊 Keywords

`robot safety` `embodied AI` `humanoid robot` `VLA safety` `AI brain` `robot control` `safe mode` `fallback` `ROS2` `autonomous robot` `robot execution` `safety verification` `collision avoidance` `force limit` `speed limit` `robot monitor` `embodied intelligence`

---

## 📄 License

MIT License — free for personal and commercial use.

---

## ⭐ Star this repo if you find it useful!
