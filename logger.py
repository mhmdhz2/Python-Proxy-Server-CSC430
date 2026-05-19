"""
logger.py — Colored, Rotating File + Console Logger
[Mohammed Hazime] Implemented the logging subsystem with color terminal output,
      rotating file handler, and thread-safe operation.
"""

import logging
import logging.handlers
import os
import threading
from config import LOG_FILE, LOG_LEVEL, LOG_MAX_BYTES, LOG_BACKUP_COUNT

RESET   = "\033[0m"
BOLD    = "\033[1m"
RED     = "\033[91m"
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
BLUE    = "\033[94m"
MAGENTA = "\033[95m"
CYAN    = "\033[96m"
WHITE   = "\033[97m"
GREY    = "\033[90m"

_LEVEL_COLORS = {
    "DEBUG":    GREY,
    "INFO":     CYAN,
    "WARNING":  YELLOW,
    "ERROR":    RED,
    "CRITICAL": BOLD + RED,
}

_lock = threading.Lock()


class ColorFormatter(logging.Formatter):
    """[Mohammed Hazime] Custom formatter that injects ANSI colors into terminal output."""

    FMT = "{color}[{levelname:<8}]{reset} {grey}{asctime}{reset}  {msg_color}{message}{reset}"

    def format(self, record: logging.LogRecord) -> str:
        level_color = _LEVEL_COLORS.get(record.levelname, WHITE)
        msg = record.getMessage()
        if "HIT" in msg:
            msg_color = GREEN
        elif "MISS" in msg or "BLOCKED" in msg or "ERROR" in msg or "FAIL" in msg:
            msg_color = RED
        elif "CONNECT" in msg or "HTTPS" in msg:
            msg_color = MAGENTA
        elif "WARNING" in msg or "WARN" in msg:
            msg_color = YELLOW
        else:
            msg_color = WHITE

        formatted = self.FMT.format(
            color=level_color,
            levelname=record.levelname,
            reset=RESET,
            grey=GREY,
            asctime=self.formatTime(record, self.datefmt),
            msg_color=msg_color,
            message=msg,
        )
        if record.exc_info:
            formatted += "\n" + self.formatException(record.exc_info)
        return formatted


class PlainFormatter(logging.Formatter):
    """[Mohammed Hazime] Plain formatter for log files (no ANSI codes)."""
    def format(self, record: logging.LogRecord) -> str:
        base = f"[{record.levelname:<8}] {self.formatTime(record, self.datefmt)}  {record.getMessage()}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def get_logger(name: str = "proxy") -> logging.Logger:
    """
    [Mohammed Hazime] Return (or create) a named logger with both console and file handlers.
    Thread-safe — can be called from any thread.
    """
    with _lock:
        logger = logging.getLogger(name)
        if logger.handlers:
            return logger  # Already configured

        level = getattr(logging, LOG_LEVEL.upper(), logging.DEBUG)
        logger.setLevel(level)

        # Console handler (colored)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(
            ColorFormatter(datefmt="%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(console_handler)

        # File handler (rotating, plain)
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(
            PlainFormatter(datefmt="%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(file_handler)

        logger.propagate = False
        return logger


# Module-level default logger
log = get_logger("proxy")
