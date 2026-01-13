"""
Structured Logging Configuration.

Provides JSON-formatted logging with correlation ID support for distributed tracing.

Aligned with Workstream 1 v3.0 spec.
"""

import logging
import json
import sys
from datetime import datetime
from typing import Dict, Any


class StructuredFormatter(logging.Formatter):
    """
    JSON formatter for structured logging.

    Outputs log records as JSON objects for easy parsing by log aggregation systems.
    """

    def format(self, record: logging.LogRecord) -> str:
        """
        Format log record as JSON.

        Args:
            record: Python logging record

        Returns:
            JSON-formatted log string
        """
        # Base log data
        log_data: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add correlation ID if present
        if hasattr(record, "correlation_id"):
            log_data["correlation_id"] = record.correlation_id

        # Add any extra fields passed via logging.info(..., extra={...})
        if hasattr(record, "__dict__"):
            for key, value in record.__dict__.items():
                # Skip standard logging attributes
                if key not in [
                    "name",
                    "msg",
                    "args",
                    "created",
                    "filename",
                    "funcName",
                    "levelname",
                    "levelno",
                    "lineno",
                    "module",
                    "msecs",
                    "pathname",
                    "process",
                    "processName",
                    "relativeCreated",
                    "thread",
                    "threadName",
                    "stack_info",
                    "exc_info",
                    "exc_text",
                    "message",
                ]:
                    log_data[key] = value

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": self.formatException(record.exc_info),
            }

        # Add source location in development
        if logging.root.level == logging.DEBUG:
            log_data["source"] = {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            }

        return json.dumps(log_data)


def setup_logging(log_level: str = "INFO", structured: bool = True) -> None:
    """
    Configure application-wide logging.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        structured: If True, use JSON formatter; if False, use human-readable format
    """
    # Convert string level to logging constant
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)

    # Set formatter
    if structured:
        formatter = StructuredFormatter()
    else:
        # Human-readable format for development
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)

    root_logger.info(
        "Logging configured",
        extra={"log_level": log_level, "structured": structured},
    )
