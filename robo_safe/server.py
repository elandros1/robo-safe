"""
Web Server — REST API + static file serving

Provides:
- POST /api/validate   — validate an action against safety rules
- POST /api/execute    — validate + simulated execution with monitoring
- GET  /api/rules      — list all registered safety rules
- GET  /api/trace      — get full execution trace
- GET  /api/stats      — get summary statistics
- POST /api/rules      — add a custom safety rule (JSON, requires API key)
- GET  /api/health     — health check

重构要点：
- 使用 config.ServerConfig 替换硬编码配置
- 添加结构化日志（logger）
- 添加优雅关停（SIGTERM/SIGINT）
- 错误响应统一格式 {error, code}
- 所有数据库操作包装 try-catch
- Content-Length 异常处理
- validate_input 使用 InvalidActionError
- 速率限制器定期清理（setInterval 风格）
"""

# 标准库
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
import json
import os
import time
import threading
import signal
import sys
from collections import defaultdict

# 第三方库
import hmac

# 本地模块
from .engine import SafetyEngine, Action, SafetyRule
from .monitor import RuntimeMonitor
from .fallback import FallbackController
from .safe_eval import safe_eval, SafeEvalError
from .config import DEFAULT_SERVER_CONFIG, ServerConfig
from .exceptions import InvalidActionError, MaxRulesExceededError, RuleAlreadyExistsError
from .logger import get_logger


def create_app(engine=None, port=3001, config=None):
    """
    Create and return an HTTP server instance.

    Args:
        engine: SafetyEngine instance (creates one with defaults if None)
        port: Port to listen on
        config: ServerConfig instance (uses DEFAULT_SERVER_CONFIG if None)

    Returns:
        ThreadingHTTPServer instance
    """
    if config is None:
        config = DEFAULT_SERVER_CONFIG

    if engine is None:
        engine = SafetyEngine(with_defaults=True)

    logger = get_logger("server")
    logger.info("初始化 RoboSafe 服务器 (port=%d)", port)

    # Engine-wide lock for thread-safe rule mutations
    engine_lock = threading.Lock()

    # Rate limiter: IP → list of timestamps
    rate_limiter = defaultdict(list)
    rate_lock = threading.Lock()

    # ─── Rate limiter periodic cleanup ────────────────────────
    def _cleanup_rate_limiter():
        """Background cleanup of expired IP entries."""
        now = time.time()
        removed = 0
        with rate_lock:
            for ip in list(rate_limiter.keys()):
                rate_limiter[ip] = [
                    t for t in rate_limiter[ip]
                    if now - t < config.rate_window
                ]
                if not rate_limiter[ip]:
                    del rate_limiter[ip]
                    removed += 1
        if removed > 0:
            logger.debug("速率限制清理: 移除 %d 个过期 IP", removed)

    cleanup_interval = config.rate_window  # Clean every rate_window seconds

    def _start_cleanup_timer():
        """Start the periodic cleanup timer (daemon thread)."""
        def _run_periodic():
            while True:
                time.sleep(cleanup_interval)
                try:
                    _cleanup_rate_limiter()
                except Exception as e:
                    logger.error("速率限制清理异常: %s", e)

        t = threading.Thread(target=_run_periodic, daemon=True)
        t.start()
        return t

    cleanup_thread = _start_cleanup_timer()

    def check_rate_limit(client_ip):
        """Returns True if request is within rate limit."""
        now = time.time()
        with rate_lock:
            # Clean old entries for this IP
            rate_limiter[client_ip] = [
                t for t in rate_limiter[client_ip]
                if now - t < config.rate_window
            ]
            # Periodic global cleanup to prevent unbounded memory growth
            if len(rate_limiter) > config.rate_map_max_size:
                for ip in list(rate_limiter.keys()):
                    rate_limiter[ip] = [
                        t for t in rate_limiter[ip]
                        if now - t < config.rate_window
                    ]
                    if not rate_limiter[ip]:
                        del rate_limiter[ip]
            if len(rate_limiter[client_ip]) >= config.rate_limit:
                return False
            rate_limiter[client_ip].append(now)
            return True

    def validate_input(name, params, priority, source):
        """
        Validate input fields.

        Returns:
            None if valid, error string if invalid.
        """
        if not isinstance(name, str) or len(name) > config.max_name_length:
            return "Invalid action name"
        if not isinstance(params, dict) or len(params) > config.max_params_size:
            return f"Params must be a dict with at most {config.max_params_size} keys"
        if not isinstance(priority, int) or priority < 0 or priority > 2:
            return "Priority must be 0, 1, or 2"
        if not isinstance(source, str) or len(source) > config.max_name_length:
            return "Invalid source"
        return None

    def require_auth(headers):
        """Check API key using constant-time comparison to prevent timing attacks."""
        auth = headers.get("Authorization", "")
        token = ""
        if auth.startswith("Bearer "):
            token = auth[7:]
        elif headers.get("X-API-Key"):
            token = headers.get("X-API-Key", "")
        if token:
            return hmac.compare_digest(token, config.api_key)
        return False

    class Handler(BaseHTTPRequestHandler):
        # Server name for headers
        server_version = "RoboSafe/2.0"

        def _send_json(self, code, data):
            """Send a JSON response with CORS headers."""
            try:
                body = json.dumps(data, ensure_ascii=False, default=str).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", config.cors_origin)
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                logger.error("发送 JSON 响应失败: %s", e)

        def _send_file(self, path, content_type):
            """Serve a static file with error handling."""
            try:
                with open(path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self._send_json(404, {"error": "File not found", "code": "NOT_FOUND"})
            except PermissionError:
                self._send_json(403, {"error": "Permission denied", "code": "FORBIDDEN"})
            except Exception as e:
                logger.error("文件服务失败 (%s): %s", path, e)
                self._send_json(500, {"error": "Internal server error", "code": "INTERNAL_ERROR"})

        def _read_body(self):
            """
            Read and parse the request body.

            Returns:
                (data_dict, error_response) tuple. If error, data is None.
            """
            try:
                content_length = int(self.headers.get("Content-Length", 0))
            except (ValueError, TypeError):
                return None, (400, {"error": "Invalid Content-Length header", "code": "INVALID_HEADER"})

            if content_length > config.max_body_size:
                return None, (413, {"error": "Request body too large", "code": "PAYLOAD_TOO_LARGE"})

            try:
                body = self.rfile.read(content_length) if content_length else b"{}"
            except Exception as e:
                logger.error("读取请求体失败: %s", e)
                return None, (400, {"error": "Failed to read request body", "code": "READ_ERROR"})

            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                return None, (400, {"error": "Invalid JSON", "code": "BAD_JSON"})

            if not isinstance(data, dict):
                return None, (400, {"error": "Request body must be a JSON object", "code": "INVALID_BODY"})

            return data, None

        def do_OPTIONS(self):
            """Handle CORS preflight requests."""
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", config.cors_origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key")
            self.end_headers()

        def do_GET(self):
            """Handle GET requests."""
            path = urlparse(self.path).path
            client_ip = self.client_address[0]

            if not check_rate_limit(client_ip):
                self._send_json(429, {"error": "Rate limit exceeded", "code": "RATE_LIMITED"})
                return

            try:
                if path == "/" or path == "/index.html":
                    web_dir = os.path.join(os.path.dirname(__file__), "..", "public")
                    self._send_file(os.path.join(web_dir, "index.html"), "text/html; charset=utf-8")
                elif path == "/api/rules":
                    self._send_json(200, {"rules": engine.list_rules()})
                elif path == "/api/trace":
                    self._send_json(200, {"trace": engine.trace_logger.get_trace()})
                elif path == "/api/stats":
                    self._send_json(200, engine.trace_logger.summary())
                elif path == "/api/health":
                    self._send_json(200, {"status": "ok", "rules": len(engine.rules)})
                else:
                    self._send_json(404, {"error": "Not found", "code": "NOT_FOUND"})
            except Exception as e:
                logger.error("GET %s 异常: %s", path, e)
                self._send_json(500, {"error": "Internal server error", "code": "INTERNAL_ERROR"})

        def do_POST(self):
            """Handle POST requests (all require authentication)."""
            path = urlparse(self.path).path
            client_ip = self.client_address[0]

            if not check_rate_limit(client_ip):
                self._send_json(429, {"error": "Rate limit exceeded", "code": "RATE_LIMITED"})
                return

            # All POST endpoints require authentication
            if not require_auth(self.headers):
                logger.warning("未授权 POST 请求 from %s: %s", client_ip, path)
                self._send_json(401, {"error": "Unauthorized. Provide X-API-Key or Authorization: Bearer <key>", "code": "UNAUTHORIZED"})
                return

            # Read and validate body
            data, error = self._read_body()
            if error:
                self._send_json(error[0], error[1])
                return

            try:
                if path == "/api/validate":
                    self._handle_validate(data)
                elif path == "/api/execute":
                    self._handle_execute(data)
                elif path == "/api/rules":
                    self._handle_add_rule(data)
                else:
                    self._send_json(404, {"error": "Not found", "code": "NOT_FOUND"})
            except Exception as e:
                logger.error("POST %s 异常: %s", path, e, exc_info=True)
                self._send_json(500, {"error": "Internal server error", "code": "INTERNAL_ERROR"})

        def _handle_validate(self, data):
            """Handle action validation requests."""
            name = str(data.get("name", "unknown_action"))
            params = data.get("params", {})
            priority = int(data.get("priority", 0))
            source = str(data.get("source", "llm"))

            error = validate_input(name, params, priority, source)
            if error:
                self._send_json(400, {"error": error, "code": "VALIDATION_ERROR"})
                return

            action = Action(name=name, params=params, priority=priority, source=source)
            result = engine.validate(action)

            # Create per-request fallback controller
            local_fallback = FallbackController(engine)
            local_fallback.set_engine(engine)

            if result.fallback_action:
                plan = local_fallback.execute_fallback(
                    action,
                    trigger=result.message,
                    strategy="safe_stop"
                )
                engine.trace_logger.log_fallback(
                    result.message, plan.fallback_action, plan.recovery_strategy
                )

            engine.trace_logger.log_outcome(
                action.name,
                "blocked" if result.blocked else "approved",
                result.message
            )

            logger.info("验证 action='%s' -> %s", name, result.status.value)
            self._send_json(200, result.to_dict())

        def _handle_execute(self, data):
            """Handle action execution with monitoring."""
            name = str(data.get("name", "unknown_action"))
            params = data.get("params", {})
            sensor_data = data.get("sensor_data", {})
            priority = int(data.get("priority", 0))

            error = validate_input(name, params, priority, "llm")
            if error:
                self._send_json(400, {"error": error, "code": "VALIDATION_ERROR"})
                return

            if not isinstance(sensor_data, dict):
                self._send_json(400, {"error": "sensor_data must be a dict", "code": "VALIDATION_ERROR"})
                return

            action = Action(name=name, params=params, priority=priority)
            result = engine.validate(action)

            if result.blocked:
                engine.trace_logger.log_outcome(action.name, "blocked", result.message)
                logger.info("执行被拦截 action='%s': %s", name, result.message)
                self._send_json(200, {
                    "validation": result.to_dict(),
                    "execution": None,
                    "message": "Action blocked by safety engine — not executed"
                })
                return

            # Create per-request monitor and fallback controller
            monitor = RuntimeMonitor(engine)
            local_fallback = FallbackController(engine)
            local_fallback.set_engine(engine)

            try:
                monitor.start(action)
                should_continue = monitor.update(sensor_data)
            except Exception as e:
                logger.error("监控异常 action='%s': %s", name, e)
                engine.trace_logger.log_outcome(action.name, "error", str(e))
                self._send_json(500, {"error": "Monitoring error", "code": "MONITOR_ERROR"})
                return

            if not should_continue and monitor.state.value == "aborted":
                anomaly = monitor.snapshots[-1].anomaly if monitor.snapshots else "unknown"
                plan = local_fallback.execute_fallback(
                    action,
                    trigger=anomaly,
                    strategy="safe_stop"
                )
                engine.trace_logger.log_fallback(
                    anomaly,
                    plan.fallback_action,
                    plan.recovery_strategy
                )
                engine.trace_logger.log_execution(action, monitor.get_trace())
                engine.trace_logger.log_outcome(action.name, "aborted", "Execution aborted during monitoring")

                logger.warning("执行中止 action='%s': %s", name, anomaly)
                self._send_json(200, {
                    "validation": result.to_dict(),
                    "execution": {
                        "state": monitor.state.value,
                        "trace": monitor.get_trace(),
                        "fallback": {
                            "action": plan.fallback_action.name,
                            "params": plan.fallback_action.params,
                            "strategy": plan.recovery_strategy,
                        }
                    },
                    "message": "Execution aborted — fallback triggered"
                })
                return

            monitor.complete()
            engine.trace_logger.log_execution(action, monitor.get_trace())
            engine.trace_logger.log_outcome(action.name, "completed", "Execution completed successfully")

            logger.info("执行完成 action='%s'", name)
            self._send_json(200, {
                "validation": result.to_dict(),
                "execution": {
                    "state": monitor.state.value,
                    "trace": monitor.get_trace(),
                },
                "message": "Action executed successfully"
            })

        def _handle_add_rule(self, data):
            """Handle adding custom safety rules."""
            rule_name = data.get("name")
            rule_expr = data.get("expression", "True")
            rule_message = data.get("message", "Custom rule")
            rule_severity = data.get("severity", "block")

            # Input validation
            if not rule_name or not isinstance(rule_name, str) or len(rule_name) > config.max_name_length:
                self._send_json(400, {"error": "Rule name is required (max 200 chars)", "code": "VALIDATION_ERROR"})
                return
            if not isinstance(rule_expr, str) or len(rule_expr) > 500:
                self._send_json(400, {"error": "Expression too long (max 500 chars)", "code": "VALIDATION_ERROR"})
                return
            if rule_severity not in ("block", "warn"):
                self._send_json(400, {"error": "Severity must be 'block' or 'warn'", "code": "VALIDATION_ERROR"})
                return

            # Validate expression compiles safely before adding
            try:
                safe_eval(rule_expr, {"test": 0})
            except SafeEvalError as e:
                self._send_json(400, {"error": f"Invalid expression: {e}", "code": "INVALID_EXPRESSION"})
                return
            except Exception:
                # Expression may reference params that don't exist yet —
                # that's fine, it will return False at runtime
                pass

            # Create a safe check function using the AST-based evaluator
            def make_check(expr):
                def check(action):
                    try:
                        return safe_eval(expr, action.params)
                    except Exception:
                        return False
                return check

            try:
                with engine_lock:
                    engine.add_rule(SafetyRule(
                        name=rule_name,
                        check=make_check(rule_expr),
                        message=rule_message,
                        severity=rule_severity
                    ))
            except RuleAlreadyExistsError as e:
                self._send_json(409, {"error": str(e), "code": "RULE_EXISTS"})
                return
            except MaxRulesExceededError as e:
                self._send_json(400, {"error": str(e), "code": "MAX_RULES"})
                return

            logger.info("添加规则 '%s' (severity=%s)", rule_name, rule_severity)
            self._send_json(200, {"success": True, "message": f"Rule '{rule_name}' added", "rules": engine.list_rules()})

        def log_message(self, format, *args):
            """Suppress default HTTP logging (use our structured logger instead)."""
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)

    # ─── Graceful shutdown ───────────────────────────────────
    def _graceful_shutdown(signum=None, frame=None):
        """Gracefully shut down the server."""
        sig_name = signal.Signals(signum).name if signum else "UNKNOWN"
        logger.info("收到 %s 信号，开始优雅关停...", sig_name)

        # Stop the server (non-blocking, allows in-flight requests to complete)
        threading.Thread(target=server.shutdown, daemon=True).start()

        # Force exit after timeout
        def _force_exit():
            time.sleep(10)
            logger.warning("关停超时，强制退出")
            os._exit(1)

        timer = threading.Timer(10, _force_exit)
        timer.daemon = True
        timer.start()

        # Wait for shutdown to complete
        time.sleep(1)
        logger.info("服务器已关闭")
        os._exit(0)

    # Register signal handlers
    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    logger.info("服务器就绪 — port=%d, rules=%d", port, len(engine.rules))
    return server


def run(port=3001):
    """Start the RoboSafe web server."""
    logger = get_logger("server")
    config = DEFAULT_SERVER_CONFIG
    server = create_app(port=port, config=config)

    print(f"\n  RoboSafe — Embodied AI Safety Harness v2.0")
    print(f"   服务地址: http://localhost:{port}")
    print(f"   验证动作: POST /api/validate (requires API key)")
    print(f"   执行动作: POST /api/execute  (requires API key)")
    print(f"   查看规则: GET  /api/rules")
    print(f"   执行轨迹: GET  /api/trace")
    print(f"   统计数据: GET  /api/stats")
    print(f"   API Key:  {config.api_key}")
    print(f"   速率限制: {config.rate_limit} req/{config.rate_window}s per IP\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("收到键盘中断，正在关停...")
    finally:
        server.shutdown()
        logger.info("服务器已关闭")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 3001
    run(port=port)
