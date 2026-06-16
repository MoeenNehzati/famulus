"""Centralized logging setup.

All modules should obtain their logger via `get_logger(__name__)` rather
than calling `print()` or `logging.getLogger()` directly, so log level,
format, and output destinations stay consistent across the project.

Configuration (read from the environment via `project.config`):
    LOG_LEVEL: Logging level name (e.g. "DEBUG", "INFO"). Defaults to "INFO".
    LOG_DIR:   Directory where "project.log" is written. Defaults to "logs".
"""

import logging
from pathlib import Path

from project import config

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_configured = False


def _configure_root() -> None:
    global _configured
    if _configured:
        return

    level_name = config.get("LOG_LEVEL", "INFO") or "INFO"
    level = getattr(logging, level_name.upper(), logging.INFO)

    log_dir = Path(config.get("LOG_DIR", "logs") or "logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("project")
    root.setLevel(level)
    root.propagate = False

    formatter = logging.Formatter(_FORMAT)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    file_handler = logging.FileHandler(log_dir / "project.log")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger for `name`.

    Args:
        name: Usually `__name__` of the calling module.

    Returns:
        A `logging.Logger` that writes to both the console and
        `<LOG_DIR>/project.log`, at the level given by `LOG_LEVEL`.
    """
    _configure_root()
    return logging.getLogger(f"project.{name}")
