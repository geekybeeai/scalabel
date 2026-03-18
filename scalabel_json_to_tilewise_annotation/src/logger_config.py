"""Centralized logging configuration for the annotation pipeline.

Call ``setup_logging()`` exactly once at application startup (in main.py).
Every other module obtains its own logger via::

    import logging
    logger = logging.getLogger(__name__)

Log files are written to a ``logs/`` directory with one file per run,
named ``run_<YYYY-MM-DD_HH-MM-SS>.log``.

Log level policy
----------------
* Project modules (``main``, ``process_image``, ``scalable_to_coco``,
  ``split_annotation``, ``logger_config``) — DEBUG and above.
* All third-party libraries (matplotlib, cv2, PIL, …) — WARNING and
  above only.  This is achieved by setting the root logger to WARNING
  and then individually lowering each project module to DEBUG.
"""

import logging
from datetime import datetime
from pathlib import Path

# Shared format: timestamp | level | module | func:line | message
LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s"
    " | %(funcName)s:%(lineno)d | %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# All project-owned modules that should log at DEBUG level.
# "__main__" covers main.py when it is executed directly as a script;
# "main" covers it when imported as a module.
_PROJECT_LOGGERS = [
    "__main__",
    "main",
    "logger_config",
    "process_image",
    "scalable_to_coco",
    "split_annotation",
    "settings",
]


def setup_logging(
    log_dir: str = "logs",
    file_level: int = logging.DEBUG,
    console_level: int = logging.INFO,
) -> None:
    """Configure logging with per-run file and console handlers.

    Strategy
    --------
    * Root logger is set to ``WARNING`` so that noisy third-party
      libraries (matplotlib, cv2, PIL, etc.) are silenced.
    * Each project module listed in ``_PROJECT_LOGGERS`` is explicitly
      set to ``DEBUG``, making their records flow through to the
      handlers where the handler's own level acts as the final filter.

    This function is idempotent: if the root logger already has handlers
    attached it returns immediately, preventing duplicate handlers.

    Args:
        log_dir: Directory where log files are stored.  Created
            automatically if it does not exist.
        file_level: Minimum severity written to the log file.
            Defaults to ``logging.DEBUG``.
        console_level: Minimum severity written to stdout/stderr.
            Defaults to ``logging.INFO``.
    """
    root = logging.getLogger()

    # Guard: only configure once
    if root.handlers:
        return

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = log_path / f"run_{timestamp}.log"

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # File handler — captures DEBUG and above for post-run audits
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(file_level)
    file_handler.setFormatter(formatter)

    # Console handler — concise INFO+ output during execution
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)

    # Root at WARNING → third-party libraries only emit warnings/errors
    root.setLevel(logging.WARNING)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Project modules get full DEBUG visibility
    for name in _PROJECT_LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG)

    logging.getLogger(__name__).info(
        "Logging initialised — log file: %s", log_file
    )
