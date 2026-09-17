# Contributing to RoboSafe

First off, thanks for taking the time to contribute! 🎉

The following is a set of guidelines for contributing to RoboSafe.

## Quick Start for Contributors

```bash
# 1. Fork & clone the repo
git clone https://github.com/YOUR_USERNAME/robo-safe.git
cd robo-safe

# 2. (Optional) Create a virtual environment
python3 -m venv .venv && source .venv/bin/activate

# 3. Run tests to make sure everything works
python3 -m pytest tests/ -v

# 4. Start the server
python3 -m robo_safe.server
```

## Development Workflow

1. **Fork** the repo and create your branch from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Write code** following the existing style:
   - Use type hints (`params: dict` → `params: Dict[str, Any]`)
   - Add docstrings to all public methods
   - Use the custom exception hierarchy from `exceptions.py`
   - Use `get_logger()` from `logger.py` — never `print()`

3. **Write tests** for any new functionality:
   ```bash
   # Test file: tests/test_robo_safe.py
   # Run: python3 -m pytest tests/ -v
   ```

4. **Commit** with a clear message:
   ```bash
   git commit -m "Add collision avoidance rule for multi-robot scenarios"
   ```

5. **Push** and open a Pull Request:
   ```bash
   git push origin feature/your-feature-name
   ```

## Code Style

| Rule | Example |
|------|---------|
| Type hints | `def validate(self, action: Action) -> ValidationResult:` |
| Docstrings | `"""Brief description. Args: ... Returns: ..."""` |
| Exceptions | `raise RuleNotFoundError(name)` — never bare `Exception` |
| Logging | `logger.info("Rule '%s' added", name)` — never `print()` |
| Thread safety | Use `threading.Lock()` for shared mutable state |
| Tests | Every new feature needs a test in `tests/test_robo_safe.py` |

## Security Rules

- **No `eval()`** — use `safe_eval.py` for expression evaluation
- **No bare `except:`** — always catch specific exceptions
- **No hardcoded secrets** — use environment variables
- **Constant-time comparison** for any token/key check
- **Input validation** on all public API entry points

## Project Structure

```
robo-safe/
├── .github/workflows/     # CI/CD automation
├── public/                # Web dashboard
├── robo_safe/             # Core package
│   ├── config.py          # Centralized configuration
│   ├── engine.py          # Safety rule engine
│   ├── monitor.py         # Runtime monitoring
│   ├── fallback.py        # Fallback strategies
│   ├── trace_logger.py    # Execution trace
│   ├── safe_eval.py       # AST expression evaluator
│   ├── exceptions.py      # Exception hierarchy
│   ├── logger.py          # Logging setup
│   └── server.py          # REST API
└── tests/                 # 83 unit tests
```

## Reporting Bugs

When filing a bug report, please include:
1. Python version (`python3 --version`)
2. OS (Linux/macOS/Windows)
3. Steps to reproduce
4. Expected vs actual behavior
5. Relevant log output

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
