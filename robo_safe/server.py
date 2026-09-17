"""
Web Server — REST API + static file serving

Provides:
- POST /api/validate   — validate an action against safety rules
- POST /api/execute    — validate + simulated execution with monitoring
- GET  /api/rules      — list all registered safety rules
- GET  /api/trace      — get full execution trace
- GET  /api/stats      — get summary statistics
- POST /api/rules      — add a custom safety rule (JSON)
- GET  /api/health     — health check
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
import json
import os
from .engine import SafetyEngine, Action, SafetyRule
from .monitor import RuntimeMonitor
from .fallback import FallbackController


def create_app(engine=None, port=3001):
    """Create and return an HTTP server instance."""
    if engine is None:
        engine = SafetyEngine()

    monitor = RuntimeMonitor(engine)
    fallback_ctrl = FallbackController()

    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, code, data):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False, default=str).encode())

        def _send_file(self, path, content_type):
            try:
                with open(path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self._send_json(404, {"error": "File not found"})

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            path = urlparse(self.path).path

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
                self._send_json(404, {"error": "Not found"})

        def do_POST(self):
            path = urlparse(self.path).path
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length else b"{}"

            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send_json(400, {"error": "Invalid JSON"})
                return

            if path == "/api/validate":
                self._handle_validate(data)
            elif path == "/api/execute":
                self._handle_execute(data)
            elif path == "/api/rules":
                self._handle_add_rule(data)
            else:
                self._send_json(404, {"error": "Not found"})

        def _handle_validate(self, data):
            name = data.get("name", "unknown_action")
            params = data.get("params", {})
            priority = data.get("priority", 0)
            source = data.get("source", "llm")

            action = Action(name=name, params=params, priority=priority, source=source)
            result = engine.validate(action)

            if result.fallback_action:
                plan = fallback_ctrl.execute_fallback(
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

            self._send_json(200, result.to_dict())

        def _handle_execute(self, data):
            name = data.get("name", "unknown_action")
            params = data.get("params", {})
            sensor_data = data.get("sensor_data", {})

            action = Action(name=name, params=params)
            result = engine.validate(action)

            if result.blocked:
                engine.trace_logger.log_outcome(action.name, "blocked", result.message)
                self._send_json(200, {
                    "validation": result.to_dict(),
                    "execution": None,
                    "message": "Action blocked by safety engine — not executed"
                })
                return

            # Simulate execution
            monitor.start(action)
            should_continue = monitor.update(sensor_data)

            if not should_continue and monitor.state.value == "aborted":
                plan = fallback_ctrl.execute_fallback(
                    action,
                    trigger=monitor.snapshots[-1].anomaly if monitor.snapshots else "unknown",
                    strategy="safe_stop"
                )
                engine.trace_logger.log_fallback(
                    monitor.snapshots[-1].anomaly if monitor.snapshots else "unknown",
                    plan.fallback_action,
                    plan.recovery_strategy
                )
                engine.trace_logger.log_execution(action, monitor.get_trace())
                engine.trace_logger.log_outcome(action.name, "aborted", "Execution aborted during monitoring")

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

            self._send_json(200, {
                "validation": result.to_dict(),
                "execution": {
                    "state": monitor.state.value,
                    "trace": monitor.get_trace(),
                },
                "message": "Action executed successfully"
            })

        def _handle_add_rule(self, data):
            rule_name = data.get("name")
            rule_expr = data.get("expression", "True")
            rule_message = data.get("message", "Custom rule")
            rule_severity = data.get("severity", "block")

            # Safely evaluate the expression against action params
            def make_check(expr):
                def check(action):
                    try:
                        safe_env = {"params": action.params, "abs": abs, "min": min, "max": max, "len": len}
                        return bool(eval(expr, {"__builtins__": {}}, safe_env))
                    except Exception:
                        return False
                return check

            engine.add_rule(SafetyRule(
                name=rule_name,
                check=make_check(rule_expr),
                message=rule_message,
                severity=rule_severity
            ))

            self._send_json(200, {"success": True, "message": f"Rule '{rule_name}' added", "rules": engine.list_rules()})

        def log_message(self, format, *args):
            pass  # suppress default logging

    server = HTTPServer(("0.0.0.0", port), Handler)
    return server


def run(port=3001):
    """Start the RoboSafe web server."""
    server = create_app(port=port)
    print(f"\n🛡️  RoboSafe — Embodied AI Safety Harness")
    print(f"   服务地址: http://localhost:{port}")
    print(f"   验证动作: POST /api/validate")
    print(f"   执行动作: POST /api/execute")
    print(f"   查看规则: GET  /api/rules")
    print(f"   执行轨迹: GET  /api/trace")
    print(f"   统计数据: GET  /api/stats\n")
    server.serve_forever()


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 3001
    run(port=port)
