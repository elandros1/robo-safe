"""
Logging Module — Structured logging configuration for RoboSafe

Design Pattern: Singleton Logger Pattern
- Centralized log configuration
- Consistent formatting across modules
- Prevents duplicate handler attachment
"""

import logging
import sys
from typing import Optional


_LOGGER_NAME = "robo_safe"
_LOGGER_INITIALIZED = False


def setup_logger(
    name: str = _LOGGER_NAME,
    level: int = logging.INFO,
    stream=None,
) -> logging.Logger:
    """
    Create or retrieve a configured logger.

    Args:
        name: Logger name (hierarchical, child of 'robo_safe')
        level: Logging level (default INFO)
        stream: Output stream (default sys.stderr)

    Returns:
        Configured Logger instance
    """
    global _LOGGER_INITIALIZED

    logger = logging.getLogger(name)

    # Prevent duplicate handlers
    if not logger.handlers:
        handler = logging.StreamHandler(stream or sys.stderr)
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.setLevel(level)
    logger.propagate = False  # Prevent double-logging via root

    if not _LOGGER_INITIALIZED:
        _LOGGER_INITIALIZED = True

    return logger


def get_logger(module_name: Optional[str] = None) -> logging.Logger:
    """
    Get a child logger under the 'robo_safe' namespace.

    Args:
        module_name: Module identifier (e.g., 'engine', 'monitor')

    Returns:
        Logger instance
    """
    base = setup_logger()
    if module_name:
        return base.getChild(module_name)
    return base
