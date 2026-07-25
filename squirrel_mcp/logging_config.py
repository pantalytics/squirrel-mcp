# SPDX-License-Identifier: MPL-2.0
# SPDX-FileCopyrightText: 2025 Andrey Ivanov <ivnv.xd@gmail.com>
# SPDX-FileCopyrightText: 2025-2026 Pantalytics B.V.
#
# Adapted from mcp-server-odoo (https://github.com/ivnvxd/mcp-server-odoo) via
# Pantalytics' odoo-mcp-pro. This file stays under the Mozilla Public License 2.0.
"""Structured logging configuration for the Squirrel MCP server.

Logs go to stderr: MCP uses stdout for JSON-RPC, so anything on stdout would
corrupt the protocol stream.
"""

import json
import logging
import logging.handlers
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional

DEFAULT_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


class StructuredFormatter(logging.Formatter):
    """Formatter that outputs structured JSON logs."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "logger": record.name,
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        for attr in ("request_id", "duration_ms", "operation", "folder", "error"):
            if hasattr(record, attr):
                log_data[attr] = getattr(record, attr)
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)


class PerformanceLogger:
    """Logger for tracking operation performance."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    @contextmanager
    def track_operation(
        self,
        operation: str,
        folder: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ):
        """Context manager for tracking operation duration.

        Usage:
            with perf_logger.track_operation("search", folder="INBOX"):
                ...
        """
        start_time = time.time()
        try:
            yield
        finally:
            duration_ms = (time.time() - start_time) * 1000
            log_data: Dict[str, Any] = {
                "operation": operation,
                "duration_ms": round(duration_ms, 2),
            }
            if folder:
                log_data["folder"] = folder
            if extra:
                log_data.update(extra)
            self.logger.info(
                f"Operation '{operation}' completed in {duration_ms:.2f}ms",
                extra=log_data,
            )
            if duration_ms > 2000:
                self.logger.warning(
                    f"Slow operation: '{operation}' took {duration_ms:.2f}ms",
                    extra=log_data,
                )


def setup_logging(
    log_level: Optional[str] = None,
    use_json: bool = False,
    log_file: Optional[str] = None,
) -> None:
    """Set up structured logging for the MCP server (to stderr)."""
    if log_level is None:
        log_level = os.getenv("SQUIRREL_MCP_LOG_LEVEL", "INFO")
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    if use_json or os.getenv("SQUIRREL_MCP_LOG_JSON", "").lower() == "true":
        formatter: logging.Formatter = StructuredFormatter()
    else:
        formatter = logging.Formatter(os.getenv("SQUIRREL_MCP_LOG_FORMAT", DEFAULT_FORMAT))

    # MUST be stderr -- stdout carries the JSON-RPC protocol.
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    if log_file or os.getenv("SQUIRREL_MCP_LOG_FILE"):
        file_path = log_file or os.getenv("SQUIRREL_MCP_LOG_FILE")
        file_handler = logging.handlers.RotatingFileHandler(
            file_path, maxBytes=10 * 1024 * 1024, backupCount=5
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    logging.getLogger("squirrel_mcp").setLevel(numeric_level)
    # Reduce third-party noise.
    logging.getLogger("imap_tools").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance."""
    return logging.getLogger(name)


class LoggingConfig:
    """Configuration wrapper for logging settings."""

    def __init__(self):
        self.log_level = os.getenv("SQUIRREL_MCP_LOG_LEVEL", "INFO")
        self.use_json = os.getenv("SQUIRREL_MCP_LOG_JSON", "false").lower() == "true"
        self.log_file = os.getenv("SQUIRREL_MCP_LOG_FILE")

    def setup(self):
        setup_logging(log_level=self.log_level, use_json=self.use_json, log_file=self.log_file)


logging_config = LoggingConfig()
perf_logger = PerformanceLogger(logging.getLogger("squirrel_mcp.performance"))
