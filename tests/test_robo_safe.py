"""
RoboSafe 单元测试套件 — 覆盖正常业务、极限边界及异常报错

测试范围：
- test_config: 配置对象不变性、默认值
- test_exceptions: 异常层级、to_dict()
- test_engine: 规则增删、验证逻辑、短路评估、线程安全、边界条件
- test_monitor: 状态转换、异常检测、falsy修复、输入验证
- test_fallback: 策略模式、不可变操作、重试计数、有界历史
- test_trace_logger: deque追加、O(1)统计、防御性拷贝、线程安全
- test_safe_eval: 正常表达式、恶意表达式、DoS防护、边界条件

运行: python3 -m pytest tests/ -v
"""

import sys
import os
import time
import threading

# 确保能找到 robo_safe 包
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from robo_safe import (
    SafetyEngine, Action, SafetyRule, ValidationResult, ExecutionStatus,
    RuntimeMonitor, MonitorState, ExecutionSnapshot,
    FallbackController, FallbackPlan,
    TraceLogger,
    safe_eval, SafeEvalError,
    SafetyThresholds, ServerConfig, DEFAULT_THRESHOLDS, DEFAULT_SERVER_CONFIG,
    RoboSafeError, RuleAlreadyExistsError, MaxRulesExceededError,
    InvalidActionError, MonitorStateError, SensorDataError,
    FallbackStrategyError, MaxRetriesExceededError,
    get_logger,
)


# ═══════════════════════════════════════════════════════════
# test_config.py — 配置对象测试
# ═══════════════════════════════════════════════════════════

class TestConfig:
    """配置对象不变性和默认值测试。"""

    def test_safety_thresholds_defaults(self):
        """正常: 默认阈值值正确。"""
        t = SafetyThresholds()
        assert t.speed_limit == 2.0
        assert t.force_limit == 50.0
        assert t.joint_angle_min == -180.0
        assert t.joint_angle_max == 180.0
        assert t.workspace_min == -1.0
        assert t.workspace_max == 2.0
        assert t.thermal_limit == 60.0
        assert t.human_proximity_min == 0.3

    def test_safety_thresholds_frozen(self):
        """边界: frozen dataclass 不允许修改属性。"""
        t = SafetyThresholds()
        with pytest.raises(AttributeError):
            t.speed_limit = 999.0

    def test_safety_thresholds_custom(self):
        """正常: 自定义阈值。"""
        t = SafetyThresholds(speed_limit=5.0, force_limit=100.0)
        assert t.speed_limit == 5.0
        assert t.force_limit == 100.0

    def test_safety_thresholds_to_dict(self):
        """正常: to_dict() 返回所有字段。"""
        d = DEFAULT_THRESHOLDS.to_dict()
        assert "speed_limit" in d
        assert "force_limit" in d
        assert len(d) == 12  # 12 个阈值

    def test_server_config_defaults(self):
        """正常: 服务器配置默认值。"""
        c = ServerConfig()
        assert c.max_body_size == 102400
        assert c.rate_limit == 30
        assert c.max_rules == 100
        assert c.fallback_history_max == 1000

    def test_server_config_frozen(self):
        """边界: ServerConfig 也是 frozen。"""
        c = ServerConfig()
        with pytest.raises(AttributeError):
            c.api_key = "hacked"


# ═══════════════════════════════════════════════════════════
# test_exceptions.py — 异常层级测试
# ═══════════════════════════════════════════════════════════

class TestExceptions:
    """自定义异常层级和 to_dict() 测试。"""

    def test_base_error(self):
        """正常: 基础异常。"""
        e = RoboSafeError("test error")
        assert str(e) == "test error"
        assert e.message == "test error"
        assert e.context == {}

    def test_error_with_context(self):
        """正常: 带上下文的异常。"""
        e = RoboSafeError("test", context={"key": "value"})
        assert e.context == {"key": "value"}

    def test_error_to_dict(self):
        """正常: to_dict() 返回结构化数据。"""
        e = RuleAlreadyExistsError("my_rule")
        d = e.to_dict()
        assert d["error"] == "RuleAlreadyExistsError"
        assert "rule_name" in d["context"]

    def test_exception_hierarchy(self):
        """边界: 所有异常都是 RoboSafeError 的子类。"""
        exceptions = [
            RuleAlreadyExistsError("r"),
            MaxRulesExceededError(100),
            InvalidActionError("field", "reason"),
            MonitorStateError("idle", "start"),
            SensorDataError("bad data"),
            FallbackStrategyError("unknown", ["safe_stop"]),
            MaxRetriesExceededError("action", 3),
            SafeEvalError("bad expr"),
        ]
        for e in exceptions:
            assert isinstance(e, RoboSafeError), f"{type(e).__name__} should be subclass of RoboSafeError"

    def test_safe_eval_error_is_robo_safe_error(self):
        """边界: SafeEvalError 同时兼容 RoboSafeError 和 Exception。"""
        e = SafeEvalError("test")
        assert isinstance(e, RoboSafeError)
        assert isinstance(e, Exception)


# ═══════════════════════════════════════════════════════════
# test_engine.py — 安全引擎测试
# ═══════════════════════════════════════════════════════════

class TestSafetyEngine:
    """安全引擎核心逻辑测试。"""

    def test_empty_engine(self):
        """正常: 空引擎不加载默认规则。"""
        engine = SafetyEngine(with_defaults=False)
        assert len(engine.rules) == 0
        assert len(engine.list_rules()) == 0

    def test_default_engine_loads_7_rules(self):
        """正常: 默认引擎加载 7 条规则。"""
        engine = SafetyEngine()
        assert len(engine.rules) == 7
        rule_names = [r["name"] for r in engine.list_rules()]
        assert "speed_limit" in rule_names
        assert "force_limit" in rule_names
        assert "thermal_safety" in rule_names

    def test_add_custom_rule(self):
        """正常: 添加自定义规则。"""
        engine = SafetyEngine(with_defaults=False)
        engine.add_rule(SafetyRule(
            name="custom_rule",
            check=lambda a: a.params.get("weight", 0) < 5,
            message="Too heavy",
            severity="block",
        ))
        assert len(engine.rules) == 1
        assert engine.list_rules()[0]["name"] == "custom_rule"

    def test_add_duplicate_rule_raises(self):
        """异常: 添加重名规则抛 RuleAlreadyExistsError。"""
        engine = SafetyEngine(with_defaults=False)
        engine.add_rule(SafetyRule(
            name="rule1", check=lambda a: True, message="msg"
        ))
        with pytest.raises(RuleAlreadyExistsError):
            engine.add_rule(SafetyRule(
                name="rule1", check=lambda a: True, message="dup"
            ))

    def test_remove_rule(self):
        """正常: 移除规则。"""
        engine = SafetyEngine(with_defaults=False)
        engine.add_rule(SafetyRule(
            name="temp", check=lambda a: True, message="msg"
        ))
        assert len(engine.rules) == 1
        engine.remove_rule("temp")
        assert len(engine.rules) == 0

    def test_validate_safe_action_passes(self):
        """正常: 安全动作通过所有规则。"""
        engine = SafetyEngine()
        action = Action(
            name="grasp_cup",
            params={"speed": 1.0, "force": 15, "target": [0.5, 0.2, 0.8],
                     "object_temp": 25, "human_distance": 1.5},
            source="llm",
        )
        result = engine.validate(action)
        assert result.status == ExecutionStatus.APPROVED
        assert len(result.passed_rules) == 7
        assert len(result.failed_rules) == 0
        assert not result.blocked

    def test_validate_speed_violation_blocks(self):
        """异常: 超速动作被拦截。"""
        engine = SafetyEngine()
        action = Action(
            name="move_arm",
            params={"speed": 3.5, "force": 20, "target": [0.6, 0.1, 0.9]},
        )
        result = engine.validate(action)
        assert result.blocked
        assert "speed_limit" in result.failed_rules

    def test_validate_thermal_violation_blocks(self):
        """异常: 高温物体被拦截。"""
        engine = SafetyEngine()
        action = Action(
            name="grasp",
            params={"speed": 0.8, "force": 10, "object_temp": 80, "human_distance": 1.0},
        )
        result = engine.validate(action)
        assert result.blocked
        assert "thermal_safety" in result.failed_rules

    def test_validate_human_proximity_blocks(self):
        """异常: 人类过近被拦截。"""
        engine = SafetyEngine()
        action = Action(
            name="move_arm",
            params={"speed": 0.5, "force": 10, "human_distance": 0.1},
        )
        result = engine.validate(action)
        assert result.blocked
        assert "human_proximity" in result.failed_rules

    def test_validate_short_circuit(self):
        """边界: 短路评估在第一个 block 规则后停止。"""
        engine = SafetyEngine()
        action = Action(
            name="move_arm",
            params={"speed": 3.5, "force": 80, "target": [3.0, -1.5, 0.8],
                     "object_temp": 80, "human_distance": 0.1},
        )
        result = engine.validate(action, short_circuit=True)
        assert result.blocked
        # 短路模式下只评估了部分规则
        assert len(result.failed_rules) < 7

    def test_validate_empty_params(self):
        """边界: 空 params 的动作（使用默认值）。"""
        engine = SafetyEngine()
        action = Action(name="noop", params={})
        result = engine.validate(action)
        # 空参数应该通过所有规则（因为 get 有默认值）
        assert result.status == ExecutionStatus.APPROVED

    def test_validate_warn_only_rule(self):
        """正常: warn 规则不阻止动作。"""
        engine = SafetyEngine(with_defaults=False)
        engine.add_rule(SafetyRule(
            name="warning_rule",
            check=lambda a: False,  # 总是失败
            message="This is just a warning",
            severity="warn",
        ))
        action = Action(name="test", params={})
        result = engine.validate(action)
        assert result.status == ExecutionStatus.APPROVED
        assert "warning_rule" in result.failed_rules

    def test_rule_check_exception_treated_as_unsafe(self):
        """异常: 规则 check 抛异常时视为不安全。"""
        engine = SafetyEngine(with_defaults=False)
        def bad_check(action):
            raise RuntimeError("check error")
        engine.add_rule(SafetyRule(
            name="bad_rule", check=bad_check, message="bad", severity="block"
        ))
        action = Action(name="test", params={})
        result = engine.validate(action)
        assert result.blocked
        assert "bad_rule" in result.failed_rules

    def test_fallback_action_generated(self):
        """正常: 被拦截的动作生成 fallback action。"""
        engine = SafetyEngine()
        action = Action(name="move_arm", params={"speed": 3.5})
        result = engine.validate(action)
        assert result.fallback_action is not None
        assert result.fallback_action.name == "safe_stop"
        assert result.fallback_action.priority == 2

    def test_validation_result_to_dict(self):
        """正常: to_dict() 包含所有字段。"""
        engine = SafetyEngine()
        action = Action(name="test", params={"speed": 1.0})
        result = engine.validate(action)
        d = result.to_dict()
        assert "status" in d
        assert "action" in d
        assert "passed_rules" in d
        assert "failed_rules" in d
        assert "timestamp" in d["action"]  # 新增 timestamp 字段

    def test_list_rules_returns_copy(self):
        """边界: list_rules 返回的是副本，修改不影响内部状态。"""
        engine = SafetyEngine()
        rules = engine.list_rules()
        rules.append({"name": "injected", "severity": "block", "message": "hack"})
        rules2 = engine.list_rules()
        assert len(rules2) == 7  # 不受影响

    def test_thread_safe_rule_addition(self):
        """边界: 多线程并发添加规则不冲突。"""
        engine = SafetyEngine(with_defaults=False)
        errors = []
        def add_rules(start, count):
            for i in range(start, start + count):
                try:
                    engine.add_rule(SafetyRule(
                        name=f"rule_{i}", check=lambda a: True, message="msg"
                    ))
                except (RuleAlreadyExistsError, MaxRulesExceededError) as e:
                    errors.append(e)
        threads = [
            threading.Thread(target=add_rules, args=(0, 20)),
            threading.Thread(target=add_rules, args=(10, 20)),
            threading.Thread(target=add_rules, args=(20, 20)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 由于重名检测，实际添加的规则数应 <= 60 且 <= max_rules(100)
        assert len(engine.rules) <= 100

    def test_custom_thresholds(self):
        """正常: 自定义阈值生效。"""
        custom = SafetyThresholds(speed_limit=10.0, force_limit=200.0)
        engine = SafetyEngine(thresholds=custom)
        action = Action(name="test", params={"speed": 5.0, "force": 100})
        result = engine.validate(action)
        assert result.status == ExecutionStatus.APPROVED


# ═══════════════════════════════════════════════════════════
# test_monitor.py — 运行时监控测试
# ═══════════════════════════════════════════════════════════

class TestRuntimeMonitor:
    """运行时监控器测试。"""

    def test_initial_state_is_idle(self):
        """正常: 初始状态为 IDLE。"""
        monitor = RuntimeMonitor(engine=None)
        assert monitor.state == MonitorState.IDLE

    def test_start_sets_running(self):
        """正常: start 后状态为 RUNNING。"""
        monitor = RuntimeMonitor(engine=None)
        action = Action(name="test", params={})
        monitor.start(action)
        assert monitor.state == MonitorState.RUNNING
        assert monitor.current_action is not None

    def test_start_none_action_raises(self):
        """异常: start(None) 抛 InvalidActionError。"""
        monitor = RuntimeMonitor(engine=None)
        with pytest.raises(InvalidActionError):
            monitor.start(None)

    def test_start_non_idle_raises(self):
        """异常: 非 IDLE 状态下 start 抛 MonitorStateError。"""
        monitor = RuntimeMonitor(engine=None)
        action = Action(name="test", params={})
        monitor.start(action)
        with pytest.raises(MonitorStateError):
            monitor.start(action)  # 已经 RUNNING

    def test_update_normal_sensor_data(self):
        """正常: 正常传感器数据不触发异常。"""
        monitor = RuntimeMonitor(engine=None)
        monitor.start(Action(name="test", params={}))
        should_continue = monitor.update({
            "force": 10,
            "speed": 1.0,
            "temperature": 25,
            "human_distance": 1.5,
        })
        assert should_continue is True
        assert monitor.state == MonitorState.RUNNING
        assert len(monitor.snapshots) == 1

    def test_update_force_spike_aborts(self):
        """异常: 力突变触发中止。"""
        monitor = RuntimeMonitor(engine=None)
        monitor.start(Action(name="test", params={}))
        should_continue = monitor.update({"force": 90, "speed": 1.0})
        assert should_continue is False
        assert monitor.state == MonitorState.ABORTED
        assert monitor.snapshots[-1].anomaly is not None
        assert "Force spike" in monitor.snapshots[-1].anomaly

    def test_update_speed_overrun_aborts(self):
        """异常: 超速触发中止。"""
        monitor = RuntimeMonitor(engine=None)
        monitor.start(Action(name="test", params={}))
        should_continue = monitor.update({"force": 10, "speed": 3.0})
        assert should_continue is False
        assert monitor.state == MonitorState.ABORTED

    def test_update_temperature_zero_not_skipped(self):
        """边界: temperature=0 不被 falsy 跳过（修复后的行为）。"""
        monitor = RuntimeMonitor(engine=None)
        monitor.start(Action(name="test", params={}))
        # temperature=0 应该被正常记录，不触发异常
        should_continue = monitor.update({"force": 10, "speed": 1.0, "temperature": 0})
        assert should_continue is True
        assert monitor.snapshots[-1].temperature == 0

    def test_update_human_distance_zero_not_skipped(self):
        """边界: human_distance=0 触发中止（修复后的行为）。"""
        monitor = RuntimeMonitor(engine=None)
        monitor.start(Action(name="test", params={}))
        should_continue = monitor.update({"force": 10, "speed": 1.0, "human_distance": 0})
        assert should_continue is False
        assert "Human too close" in monitor.snapshots[-1].anomaly

    def test_update_non_dict_sensor_data_raises(self):
        """异常: 非 dict 传感器数据抛 SensorDataError。"""
        monitor = RuntimeMonitor(engine=None)
        monitor.start(Action(name="test", params={}))
        with pytest.raises(SensorDataError):
            monitor.update("not a dict")
        with pytest.raises(SensorDataError):
            monitor.update(None)
        with pytest.raises(SensorDataError):
            monitor.update(123)

    def test_update_when_not_running_returns_false(self):
        """边界: 非 RUNNING 状态下 update 返回 False。"""
        monitor = RuntimeMonitor(engine=None)
        # IDLE 状态下调用 update
        result = monitor.update({"force": 10, "speed": 1.0})
        assert result is False

    def test_complete_sets_completed(self):
        """正常: complete 后状态为 COMPLETED。"""
        monitor = RuntimeMonitor(engine=None)
        monitor.start(Action(name="test", params={}))
        monitor.update({"force": 10, "speed": 1.0})
        monitor.complete()
        assert monitor.state == MonitorState.COMPLETED

    def test_abort_sets_aborted(self):
        """正常: abort 后状态为 ABORTED。"""
        monitor = RuntimeMonitor(engine=None)
        monitor.start(Action(name="test", params={}))
        monitor.abort("manual abort")
        assert monitor.state == MonitorState.ABORTED

    def test_on_abort_callback_called(self):
        """正常: 异常时调用 on_abort_callback。"""
        called_with = []
        monitor = RuntimeMonitor(engine=None)
        monitor.on_abort_callback = lambda snap: called_with.append(snap)
        monitor.start(Action(name="test", params={}))
        monitor.update({"force": 90, "speed": 1.0})
        assert len(called_with) == 1
        assert called_with[0].anomaly is not None

    def test_get_trace_returns_list(self):
        """正常: get_trace 返回可序列化的列表。"""
        monitor = RuntimeMonitor(engine=None)
        monitor.start(Action(name="test", params={}))
        monitor.update({"force": 10, "speed": 1.0, "temperature": 25})
        trace = monitor.get_trace()
        assert isinstance(trace, list)
        assert len(trace) == 1
        assert "timestamp" in trace[0]
        assert "force" in trace[0]

    def test_custom_thresholds(self):
        """正常: 自定义阈值生效。"""
        custom = SafetyThresholds(rt_force_spike=50.0)
        monitor = RuntimeMonitor(engine=None, thresholds=custom)
        monitor.start(Action(name="test", params={}))
        # 60N > 50N (custom threshold), 应该触发
        should_continue = monitor.update({"force": 60, "speed": 1.0})
        assert should_continue is False


# ═══════════════════════════════════════════════════════════
# test_fallback.py — 降级控制器测试
# ═══════════════════════════════════════════════════════════

class TestFallbackController:
    """降级控制器测试。"""

    def test_safe_stop_strategy(self):
        """正常: safe_stop 策略生成正确动作。"""
        ctrl = FallbackController()
        action = Action(name="move_arm", params={"speed": 3.5})
        plan = ctrl.execute_fallback(action, "speed violation", "safe_stop")
        assert plan.fallback_action.name == "safe_stop"
        assert plan.recovery_strategy == "safe_stop"
        assert plan.fallback_action.priority == 2

    def test_graceful_retreat_strategy(self):
        """正常: graceful_retreat 策略。"""
        ctrl = FallbackController()
        action = Action(name="move_arm", params={"speed": 3.5})
        plan = ctrl.execute_fallback(action, "trigger", "graceful_retreat")
        assert plan.fallback_action.name == "move_to_home"
        assert plan.fallback_action.params["speed"] == 0.3

    def test_degrade_strategy_creates_copy(self):
        """正常: degrade 策略创建新 params（不可变操作）。"""
        ctrl = FallbackController()
        original_params = {"speed": 3.5, "force": 80}
        action = Action(name="move_arm", params=original_params)
        plan = ctrl.execute_fallback(action, "trigger", "degrade")
        # 原始 params 不被修改
        assert original_params["speed"] == 3.5
        assert original_params["force"] == 80
        # 降级后的 params 是新的
        assert plan.fallback_action.params["speed"] <= 0.5
        assert plan.fallback_action.params.get("degraded") is True

    def test_retry_strategy_increments_counter(self):
        """正常: retry 策略增加重试计数。"""
        ctrl = FallbackController()
        action = Action(name="move_arm", params={"speed": 1.0})
        ctrl.execute_fallback(action, "trigger1", "retry")
        ctrl.execute_fallback(action, "trigger2", "retry")
        assert len(ctrl.fallback_history) == 2

    def test_retry_exceeds_max_raises(self):
        """异常: 超过最大重试次数抛 MaxRetriesExceededError。"""
        ctrl = FallbackController()
        action = Action(name="move_arm", params={"speed": 1.0})
        # 默认最大 3 次
        ctrl.execute_fallback(action, "t1", "retry")
        ctrl.execute_fallback(action, "t2", "retry")
        ctrl.execute_fallback(action, "t3", "retry")
        with pytest.raises(MaxRetriesExceededError):
            ctrl.execute_fallback(action, "t4", "retry")

    def test_unknown_strategy_raises(self):
        """异常: 未知策略抛 FallbackStrategyError。"""
        ctrl = FallbackController()
        action = Action(name="test", params={})
        with pytest.raises(FallbackStrategyError):
            ctrl.execute_fallback(action, "trigger", "unknown_strategy")

    def test_history_is_bounded(self):
        """边界: 历史记录有上限。"""
        ctrl = FallbackController()
        max_size = DEFAULT_SERVER_CONFIG.fallback_history_max
        for i in range(max_size + 50):
            try:
                ctrl.execute_fallback(
                    Action(name=f"action_{i}", params={}),
                    f"trigger_{i}",
                    "safe_stop",
                )
            except Exception:
                pass
        assert len(ctrl.fallback_history) <= max_size

    def test_get_history_returns_copy(self):
        """边界: get_history 返回防御性拷贝。"""
        ctrl = FallbackController()
        ctrl.execute_fallback(
            Action(name="test", params={}), "trigger", "safe_stop"
        )
        history = ctrl.get_history()
        history.append({"fake": "entry"})
        history2 = ctrl.get_history()
        assert len(history2) == 1  # 不受外部修改影响


# ═══════════════════════════════════════════════════════════
# test_trace_logger.py — 轨迹日志测试
# ═══════════════════════════════════════════════════════════

class TestTraceLogger:
    """轨迹日志测试。"""

    def test_log_validation(self):
        """正常: 记录验证事件。"""
        logger = TraceLogger(max_entries=100)
        action = Action(name="test", params={"speed": 1.0})
        result = ValidationResult(
            status=ExecutionStatus.APPROVED,
            action=action,
            passed_rules=["rule1"],
            failed_rules=[],
            message="All passed",
        )
        logger.log_validation(result)
        trace = logger.get_trace()
        assert len(trace) == 1
        assert trace[0]["event"] == "validation"

    def test_log_execution(self):
        """正常: 记录执行事件。"""
        logger = TraceLogger(max_entries=100)
        action = Action(name="test", params={})
        logger.log_execution(action, [{"force": 10, "anomaly": None}])
        trace = logger.get_trace()
        assert len(trace) == 1
        assert trace[0]["event"] == "execution"

    def test_log_fallback(self):
        """正常: 记录降级事件。"""
        logger = TraceLogger(max_entries=100)
        action = Action(name="safe_stop", params={})
        logger.log_fallback("trigger", action, "safe_stop")
        trace = logger.get_trace()
        assert trace[0]["event"] == "fallback"
        assert trace[0]["trigger"] == "trigger"

    def test_log_outcome(self):
        """正常: 记录结果事件。"""
        logger = TraceLogger(max_entries=100)
        logger.log_outcome("test_action", "approved", "All good")
        trace = logger.get_trace()
        assert trace[0]["event"] == "outcome"
        assert trace[0]["outcome"] == "approved"

    def test_max_entries_auto_trim(self):
        """边界: 超过 max_entries 自动裁剪。"""
        logger = TraceLogger(max_entries=5)
        for i in range(10):
            logger.log_outcome(f"action_{i}", "approved")
        trace = logger.get_trace()
        assert len(trace) == 5  # deque(maxlen=5) 自动裁剪
        # 应该保留最后 5 条
        assert trace[0]["action"] == "action_5"
        assert trace[-1]["action"] == "action_9"

    def test_get_trace_returns_copy(self):
        """边界: get_trace 返回防御性拷贝。"""
        logger = TraceLogger(max_entries=100)
        logger.log_outcome("test", "approved")
        trace = logger.get_trace()
        trace.append({"fake": "entry"})
        trace2 = logger.get_trace()
        assert len(trace2) == 1  # 不受外部修改影响

    def test_summary_o1(self):
        """正常: summary() 通过增量计数器 O(1) 返回。"""
        logger = TraceLogger(max_entries=100)
        action = Action(name="test", params={})
        result = ValidationResult(
            status=ExecutionStatus.BLOCKED,
            action=action,
            passed_rules=[],
            failed_rules=["rule1"],
            message="blocked",
        )
        logger.log_validation(result)
        logger.log_outcome("test", "blocked")
        summary = logger.summary()
        assert summary["total_events"] == 2
        assert summary["blocked"] == 1
        assert "block_rate" in summary

    def test_clear_resets_counters(self):
        """正常: clear() 重置计数器。"""
        logger = TraceLogger(max_entries=100)
        logger.log_outcome("test", "approved")
        assert len(logger.get_trace()) == 1
        logger.clear()
        assert len(logger.get_trace()) == 0
        summary = logger.summary()
        assert summary["total_events"] == 0

    def test_log_none_result_skipped(self):
        """异常: log_validation(None) 跳过记录。"""
        logger = TraceLogger(max_entries=100)
        logger.log_validation(None)
        assert len(logger.get_trace()) == 0

    def test_log_execution_none_action_skipped(self):
        """异常: log_execution(None, ...) 跳过记录。"""
        logger = TraceLogger(max_entries=100)
        logger.log_execution(None, [])
        assert len(logger.get_trace()) == 0


# ═══════════════════════════════════════════════════════════
# test_safe_eval.py — 安全表达式评估器测试
# ═══════════════════════════════════════════════════════════

class TestSafeEval:
    """安全表达式评估器测试。"""

    def test_simple_comparison(self):
        """正常: 简单比较表达式。"""
        assert safe_eval("params.get('speed', 0) < 2.0", {"speed": 1.0}) is True
        assert safe_eval("params.get('speed', 0) < 2.0", {"speed": 3.0}) is False

    def test_boolean_logic(self):
        """正常: 布尔逻辑。"""
        expr = "params.get('speed', 0) < 2.0 and params.get('force', 0) < 50"
        assert safe_eval(expr, {"speed": 1.0, "force": 10}) is True
        assert safe_eval(expr, {"speed": 3.0, "force": 10}) is False

    def test_or_logic(self):
        """正常: or 逻辑。"""
        expr = "params.get('speed', 0) > 2.0 or params.get('force', 0) > 50"
        assert safe_eval(expr, {"speed": 3.0, "force": 10}) is True
        assert safe_eval(expr, {"speed": 1.0, "force": 60}) is True
        assert safe_eval(expr, {"speed": 1.0, "force": 10}) is False

    def test_not_operator(self):
        """正常: not 操作符。"""
        assert safe_eval("not params.get('blocked', False)", {"blocked": False}) is True
        assert safe_eval("not params.get('blocked', False)", {"blocked": True}) is False

    def test_arithmetic(self):
        """正常: 算术运算。"""
        assert safe_eval("params.get('x', 0) + params.get('y', 0) < 10", {"x": 3, "y": 4}) is True
        assert safe_eval("params.get('x', 0) * 2 > 10", {"x": 6}) is True

    def test_safe_functions(self):
        """正常: 白名单函数调用。"""
        assert safe_eval("abs(params.get('x', 0)) < 5", {"x": -3}) is True
        assert safe_eval("min(params.get('a', 0), params.get('b', 0)) < 5", {"a": 3, "b": 10}) is True
        assert safe_eval("max(params.get('a', 0), params.get('b', 0)) > 5", {"a": 3, "b": 10}) is True

    def test_subscript_access(self):
        """正常: 下标访问。"""
        assert safe_eval("params['speed'] < 2.0", {"speed": 1.0}) is True

    def test_dangerous_import_rejected(self):
        """安全: __import__ 被拒绝。"""
        with pytest.raises(SafeEvalError):
            safe_eval('__import__("os").system("ls")', {})

    def test_dangerous_eval_rejected(self):
        """安全: eval() 调用被拒绝。"""
        with pytest.raises(SafeEvalError):
            safe_eval('eval("1+1")', {})

    def test_power_operator_rejected(self):
        """安全: Pow 运算符被拒绝（DoS 防护）。"""
        with pytest.raises(SafeEvalError):
            safe_eval("2**999999", {})

    def test_deep_nesting_rejected(self):
        """安全: 深层嵌套被拒绝。"""
        deep_expr = "1" + " + 1" * 30
        with pytest.raises(SafeEvalError):
            safe_eval(deep_expr, {})

    def test_builtins_access_rejected(self):
        """安全: __builtins__ 访问被拒绝。"""
        with pytest.raises(SafeEvalError):
            safe_eval("__builtins__", {})

    def test_attribute_access_rejected(self):
        """安全: 非白名单属性被拒绝。"""
        with pytest.raises(SafeEvalError):
            safe_eval("params.__class__", {})

    def test_syntax_error_raises(self):
        """异常: 语法错误抛 SafeEvalError。"""
        with pytest.raises(SafeEvalError):
            safe_eval("params.get(", {})

    def test_too_long_expression_rejected(self):
        """边界: 超长表达式被拒绝。"""
        long_expr = "1 + " * 600 + "1"
        with pytest.raises(SafeEvalError):
            safe_eval(long_expr, {})

    def test_empty_expression(self):
        """边界: 空字符串表达式。"""
        # 空字符串会触发 SyntaxError -> SafeEvalError
        with pytest.raises(SafeEvalError):
            safe_eval("", {})

    def test_true_false_none_constants(self):
        """正常: True/False/None 常量。"""
        assert safe_eval("True", {}) is True
        assert safe_eval("False", {}) is False

    def test_list_literal(self):
        """正常: 列表字面量。"""
        assert safe_eval("len([1, 2, 3]) == 3", {}) is True

    def test_dict_get_with_default(self):
        """正常: params.get() 带默认值。"""
        assert safe_eval("params.get('missing', 0) == 0", {}) is True

    def test_compare_chaining(self):
        """正常: 比较链。"""
        assert safe_eval("0 < params.get('x', 5) < 10", {"x": 5}) is True
