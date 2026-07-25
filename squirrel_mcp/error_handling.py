# SPDX-License-Identifier: MPL-2.0
# SPDX-FileCopyrightText: 2025 Andrey Ivanov <ivnv.xd@gmail.com>
# SPDX-FileCopyrightText: 2025-2026 Pantalytics B.V.
#
# Adapted from mcp-server-odoo (https://github.com/ivnvxd/mcp-server-odoo) via
# Pantalytics' odoo-mcp-pro. This file stays under the Mozilla Public License 2.0.
"""Error handling for the Squirrel MCP server.

A small exception hierarchy with categories/severity, plus a lightweight handler
that keeps counters and recent errors for the health endpoint. Messages are
sanitized before they leave the process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Dict, List, Optional

from mcp.types import ErrorData

from .error_sanitizer import ErrorSanitizer
from .logging_config import get_logger

logger = get_logger(__name__)


class ErrorCategory(Enum):
    AUTHENTICATION = auto()
    PERMISSION = auto()
    NOT_FOUND = auto()
    VALIDATION = auto()
    CONNECTION = auto()
    SYSTEM = auto()
    CONFIGURATION = auto()


class ErrorSeverity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ErrorContext:
    """Context information for an error."""

    operation: Optional[str] = None
    folder: Optional[str] = None
    uid: Optional[str] = None
    additional_info: Dict[str, Any] = field(default_factory=dict)


class MCPError(Exception):
    """Base exception for Squirrel MCP errors."""

    _CODES = {
        ErrorCategory.AUTHENTICATION: "AUTH_ERROR",
        ErrorCategory.PERMISSION: "PERMISSION_DENIED",
        ErrorCategory.NOT_FOUND: "NOT_FOUND",
        ErrorCategory.VALIDATION: "VALIDATION_ERROR",
        ErrorCategory.CONNECTION: "CONNECTION_ERROR",
        ErrorCategory.SYSTEM: "SYSTEM_ERROR",
        ErrorCategory.CONFIGURATION: "CONFIG_ERROR",
    }

    def __init__(
        self,
        message: str,
        category: ErrorCategory,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        context: Optional[ErrorContext] = None,
    ):
        super().__init__(message)
        self.message = message
        self.category = category
        self.severity = severity
        self.code = code or self._CODES.get(category, "UNKNOWN_ERROR")
        self.details = details or {}
        self.context = context or ErrorContext()
        self.timestamp = datetime.now()

    def to_mcp_error(self) -> ErrorData:
        """Convert to MCP-compliant error format (sanitized)."""
        return ErrorData(
            code=-32000,
            message=ErrorSanitizer.sanitize_message(self.message),
            data={
                "code": self.code,
                "details": ErrorSanitizer.sanitize_error_details(self.details),
            },
        )


class AuthenticationError(MCPError):
    def __init__(self, message: str, **kwargs):
        super().__init__(message, ErrorCategory.AUTHENTICATION, ErrorSeverity.HIGH, **kwargs)


class PermissionError(MCPError):  # noqa: A001 - intentional MCP-domain shadow
    def __init__(self, message: str, **kwargs):
        super().__init__(message, ErrorCategory.PERMISSION, ErrorSeverity.MEDIUM, **kwargs)


class NotFoundError(MCPError):
    def __init__(self, message: str, **kwargs):
        super().__init__(message, ErrorCategory.NOT_FOUND, ErrorSeverity.LOW, **kwargs)


class ValidationError(MCPError):
    def __init__(self, message: str, **kwargs):
        super().__init__(message, ErrorCategory.VALIDATION, ErrorSeverity.LOW, **kwargs)


class ConnectionError(MCPError):  # noqa: A001 - intentional MCP-domain shadow
    def __init__(self, message: str, **kwargs):
        super().__init__(message, ErrorCategory.CONNECTION, ErrorSeverity.HIGH, **kwargs)


class SystemError(MCPError):  # noqa: A001 - intentional MCP-domain shadow
    def __init__(self, message: str, **kwargs):
        super().__init__(message, ErrorCategory.SYSTEM, ErrorSeverity.CRITICAL, **kwargs)


class ConfigurationError(MCPError):
    def __init__(self, message: str, **kwargs):
        super().__init__(message, ErrorCategory.CONFIGURATION, ErrorSeverity.HIGH, **kwargs)


class ErrorHandler:
    """Keeps error metrics and recent errors for health reporting."""

    def __init__(self) -> None:
        self._total = 0
        self._by_category: Dict[str, int] = {}
        self._recent: List[Dict[str, Any]] = []

    def handle_error(self, error: Exception, context: Optional[ErrorContext] = None) -> None:
        """Record an error, then re-raise it wrapped as an MCPError.

        Known ``MCPError`` instances propagate unchanged; anything else becomes a
        ``SystemError`` so the client always gets a sanitized, categorized error.
        """
        self._record(error)
        if isinstance(error, MCPError):
            raise error
        raise SystemError(str(error), context=context) from error

    def _record(self, error: Exception) -> None:
        self._total += 1
        category = error.category.name if isinstance(error, MCPError) else "SYSTEM"
        self._by_category[category] = self._by_category.get(category, 0) + 1
        self._recent.append(
            {
                "category": category,
                "message": ErrorSanitizer.sanitize_message(str(error)),
                "timestamp": datetime.now().isoformat(),
            }
        )
        self._recent = self._recent[-20:]
        logger.error("Handled error [%s]: %s", category, error)

    def get_metrics(self) -> Dict[str, Any]:
        return {"total_errors": self._total, "errors_by_category": dict(self._by_category)}

    def get_recent_errors(self, limit: int = 5) -> List[Dict[str, Any]]:
        return self._recent[-limit:]


# Global handler instance (health endpoint reads its metrics).
error_handler = ErrorHandler()
