from setuptools import setup, find_packages

setup(
    name="robo-safe",
    version="1.0.0",
    description="Embodied AI safety harness for humanoid robots — intercept, validate, and safeguard every AI brain action",
    long_description="""
RoboSafe — Embodied AI Safety Harness
=====================================

A lightweight safety verification layer for humanoid robot AI brains.

Intercepts every action the AI brain outputs, validates it against safety rules,
monitors execution in real-time, and auto-falls-back to safe mode on danger.

Features:
- Pre-execution safety validation (rule engine)
- Real-time execution monitoring (sensor-based)
- Automatic fallback to safe mode (safe stop / graceful retreat / degrade)
- Full-chain execution trace logging (brain → safety → execution → outcome)
- Built-in rules: speed limit, force limit, joint angle, workspace boundary,
  thermal safety, human proximity
- Custom rule API (add rules via JSON expression)
- Web dashboard for visualized safety validation
- REST API for integration with any robot system

Usage:
    from robo_safe import SafetyEngine, Action

    engine = SafetyEngine()
    action = Action(name="move_arm", params={"speed": 3.5})
    result = engine.validate(action)
    print(result.blocked)  # True — speed exceeds 2.0 m/s
""",
    long_description_content_type="text/x-rst",
    author="RoboSafe Project",
    license="MIT",
    keywords="robot safety embodied-ai humanoid-robot vla robot-safety ai-safety robot-control ros2 autonomous-robot",
    packages=find_packages(),
    install_requires=[],
    python_requires=">=3.8",
    entry_points={
        "console_scripts": [
            "robo-safe=robo_safe.server:run",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
)
