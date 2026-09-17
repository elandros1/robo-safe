"""
Safe Expression Evaluator — Replaces eval() with a restricted AST-based parser

Only allows:
- Comparison: ==, !=, <, >, <=, >=
- Boolean: and, or, not
- Arithmetic: +, -, *, /, //, %
- Function calls: abs, min, max, len, round, int, float
- Subscript: params.get(...), params[...]
- Constants: numbers, strings, True, False, None
- Attribute access: .get only

重构要点：
- 删除 visit_Call 中的死代码分支
- 修复 visit_Subscript：兼容 Python 3.9+（不再使用 ast.Index）
- 添加表达式长度限制（max 1000 字符）
- 添加超时机制：signal.alarm (Unix)，默认 2 秒
- 添加 logger 记录评估失败
- SafeEvalError 继承自 RoboSafeError（向后兼容）
"""

# 标准库
import ast
import operator
import signal

# 本地模块
from .exceptions import RoboSafeError
from .logger import get_logger


# Whitelisted operations (no Pow — prevents 2**999999 DoS)
_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.And: lambda a, b: a and b,
    ast.Or: lambda a, b: a or b,
}

# Maximum AST depth to prevent stack overflow via deeply nested expressions
_MAX_DEPTH = 20

# 表达式最大长度（字符数）
_MAX_EXPR_LENGTH = 1000

# 评估超时时间（秒）
_EVAL_TIMEOUT = 2

# Whitelisted functions
_SAFE_FUNCS = {
    "abs": abs,
    "min": min,
    "max": max,
    "len": len,
    "round": round,
    "int": int,
    "float": float,
    "all": all,
    "any": any,
    "sum": sum,
}

# Whitelisted attribute names (only .get for dict access)
_SAFE_ATTRS = {"get", "keys", "values", "items"}

_logger = get_logger("safe_eval")


class SafeEvalError(RoboSafeError):
    """
    Raised when an expression contains unsafe constructs.

    继承自 RoboSafeError（同时向后兼容，因为 RoboSafeError 继承自 Exception）。
    """
    pass


def _timeout_handler(signum, frame):
    """signal.alarm 的超时回调。"""
    raise SafeEvalError(f"Expression evaluation timed out (max {_EVAL_TIMEOUT}s)")


def safe_eval(expr: str, params: dict) -> bool:
    """
    Safely evaluate a boolean expression against action params.

    Args:
        expr: Expression string, e.g. "params.get('speed', 0) < 2.0"
        params: The action params dict

    Returns:
        Boolean result of the expression

    Raises:
        SafeEvalError: If the expression contains unsafe constructs,
                       exceeds length limit, or times out.
    """
    # 表达式长度限制
    if len(expr) > _MAX_EXPR_LENGTH:
        raise SafeEvalError(
            f"Expression too long (max {_MAX_EXPR_LENGTH} characters)",
            context={"length": len(expr), "max": _MAX_EXPR_LENGTH},
        )

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        _logger.warning("表达式语法错误: %s", expr[:200])
        raise SafeEvalError(f"Syntax error: {e}")

    evaluator = _SafeVisitor(params)

    try:
        result = _eval_with_timeout(evaluator, tree)
    except SafeEvalError:
        raise
    except Exception as e:
        _logger.warning("表达式评估失败: %s", str(e))
        raise SafeEvalError(f"Evaluation error: {e}")

    return bool(result)


def _eval_with_timeout(evaluator: "_SafeVisitor", tree: ast.Expression):
    """
    评估 AST，带超时保护。

    优先使用 signal.alarm（仅 Unix 主线程可用）；
    在非主线程或不支持 signal 的平台上，回退为直接评估
    （仍有深度和长度限制作为保护）。
    """
    use_signal = False
    old_handler = None

    # 尝试使用 signal.alarm 设置超时（仅 Unix 主线程）
    try:
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(_EVAL_TIMEOUT)
        use_signal = True
    except (ValueError, AttributeError, OSError):
        # ValueError: 不在主线程
        # AttributeError/OSError: 平台不支持 SIGALRM
        pass

    try:
        return evaluator.visit(tree.body)
    finally:
        if use_signal:
            signal.alarm(0)  # 取消定时器
            signal.signal(signal.SIGALRM, old_handler)  # 恢复原处理器


class _SafeVisitor(ast.NodeVisitor):
    """AST visitor that only allows safe operations."""

    def __init__(self, params: dict):
        self.params = params
        self._depth = 0

    def visit(self, node):
        self._depth += 1
        if self._depth > _MAX_DEPTH:
            raise SafeEvalError("Expression too deeply nested (max depth 20)")
        result = super().visit(node)
        self._depth -= 1
        return result

    def visit_Expression(self, node):
        return self.visit(node.body)

    def visit_BoolOp(self, node):
        op_func = _OPS.get(type(node.op))
        if not op_func:
            raise SafeEvalError(f"Boolean op {type(node.op).__name__} not allowed")
        values = [self.visit(v) for v in node.values]
        result = values[0]
        for v in values[1:]:
            result = op_func(result, v)
        return result

    def visit_BinOp(self, node):
        op_func = _OPS.get(type(node.op))
        if not op_func:
            raise SafeEvalError(f"Binary op {type(node.op).__name__} not allowed")
        left = self.visit(node.left)
        right = self.visit(node.right)
        return op_func(left, right)

    def visit_UnaryOp(self, node):
        if isinstance(node.op, ast.Not):
            return not self.visit(node.operand)
        op_func = _OPS.get(type(node.op))
        if not op_func:
            raise SafeEvalError(f"Unary op {type(node.op).__name__} not allowed")
        return op_func(self.visit(node.operand))

    def visit_Compare(self, node):
        left = self.visit(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            op_func = _OPS.get(type(op))
            if not op_func:
                raise SafeEvalError(f"Comparison op {type(op).__name__} not allowed")
            right = self.visit(comparator)
            if not op_func(left, right):
                return False
            left = right
        return True

    def visit_Call(self, node):
        func_name = None
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            # Allow e.g. params.get(...)
            obj = self.visit(node.func.value)
            attr_name = node.func.attr
            if attr_name not in _SAFE_ATTRS:
                raise SafeEvalError(f"Attribute '{attr_name}' not allowed")
            args = [self.visit(a) for a in node.args]
            kwargs = {kw.arg: self.visit(kw.value) for kw in node.keywords if kw.arg}
            return getattr(obj, attr_name)(*args, **kwargs)
        else:
            raise SafeEvalError("Unsupported function call type")

        if func_name not in _SAFE_FUNCS:
            raise SafeEvalError(f"Function '{func_name}' not allowed")

        args = [self.visit(a) for a in node.args]
        kwargs = {kw.arg: self.visit(kw.value) for kw in node.keywords if kw.arg}
        return _SAFE_FUNCS[func_name](*args, **kwargs)

    def visit_Attribute(self, node):
        attr_name = node.attr
        if attr_name not in _SAFE_ATTRS:
            raise SafeEvalError(f"Attribute '{attr_name}' not allowed")
        obj = self.visit(node.value)
        return getattr(obj, attr_name)

    def visit_Name(self, node):
        if node.id == "params":
            return self.params
        if node.id == "True":
            return True
        if node.id == "False":
            return False
        if node.id == "None":
            return None
        if node.id in _SAFE_FUNCS:
            return _SAFE_FUNCS[node.id]
        raise SafeEvalError(f"Name '{node.id}' not allowed")

    def visit_Constant(self, node):
        return node.value

    def visit_List(self, node):
        return [self.visit(e) for e in node.elts]

    def visit_Tuple(self, node):
        return tuple(self.visit(e) for e in node.elts)

    def visit_Dict(self, node):
        keys = [self.visit(k) for k in node.keys]
        values = [self.visit(v) for v in node.values]
        return dict(zip(keys, values))

    def visit_Subscript(self, node):
        """兼容 Python 3.9+：直接处理 node.slice，不再使用废弃的 ast.Index。"""
        obj = self.visit(node.value)
        key = self.visit(node.slice)
        return obj[key]

    def generic_visit(self, node):
        raise SafeEvalError(f"AST node type {type(node).__name__} not allowed")
