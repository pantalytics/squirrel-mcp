# SPDX-License-Identifier: MPL-2.0
# SPDX-FileCopyrightText: 2025 Andrey Ivanov <ivnv.xd@gmail.com>
# SPDX-FileCopyrightText: 2025-2026 Pantalytics B.V.
#
# Adapted from mcp-server-odoo (https://github.com/ivnvxd/mcp-server-odoo) via
# Pantalytics' odoo-mcp-pro. This file stays under the Mozilla Public License 2.0.
"""Error message sanitizer for the Squirrel MCP server.

Strips internal implementation details (file paths, tracebacks, module names)
from error messages before they reach the client, and -- importantly -- keeps
credentials out of any surfaced text.
"""

import re
from typing import Any, Dict


class ErrorSanitizer:
    """Sanitizes error messages to remove internal / sensitive details."""

    PATTERNS_TO_REMOVE = [
        (r'(File|file)\s*"[^"]+\.py"', "file"),
        (r"(/[^/\s]+)+/[^/\s]+\.py", ""),
        (r",?\s*line\s+\d+", ""),
        (r"Traceback \(most recent call last\):", ""),
        (r"squirrel_mcp\.[a-zA-Z_\.]+:", ""),
        (r"<class \'[^\']+\'>", ""),
        (r"\s+at\s+0x[0-9a-fA-F]+", ""),
        (r"in\s+<[^>]+>", ""),
    ]

    # Map raw provider errors to friendly, non-leaky messages.
    ERROR_MAPPINGS = {
        r"\[AUTHENTICATIONFAILED\]": "Authentication failed: check your email and password",
        r"authentication failed": "Authentication failed: check your email and password",
        r"Invalid credentials": "Authentication failed: check your email and password",
        r"Connection refused": "Cannot connect to the mail server",
        r"Name or service not known": "Mail server host could not be resolved",
        r"timed out": "The mail server did not respond in time",
        r"\[NONEXISTENT\]": "That folder or message does not exist",
        r"command .+ illegal in state": "The mail server rejected the request in its current state",
    }

    @classmethod
    def sanitize_message(cls, message: str) -> str:
        """Sanitize an error message for user consumption."""
        if not message:
            return "An error occurred"

        for pattern, replacement in cls.ERROR_MAPPINGS.items():
            if re.search(pattern, message, re.IGNORECASE):
                return replacement

        sanitized = message
        for pattern, replacement in cls.PATTERNS_TO_REMOVE:
            sanitized = re.sub(pattern, replacement, sanitized, flags=re.MULTILINE)
        sanitized = cls._redact_secrets(sanitized)
        sanitized = re.sub(r"\s+", " ", sanitized).strip()

        if not sanitized or sanitized == "file" or len(sanitized) < 5:
            return "An error occurred while processing your request"
        if sanitized[0].islower():
            sanitized = sanitized[0].upper() + sanitized[1:]
        return sanitized

    @classmethod
    def _redact_secrets(cls, text: str) -> str:
        """Best-effort redaction of anything that looks like a credential."""
        text = re.sub(r"(?i)(password|passwd|pwd)\s*[=:]\s*\S+", r"\1=***", text)
        text = re.sub(r"(?i)LOGIN\s+\S+\s+\S+", "LOGIN *** ***", text)
        return text

    @classmethod
    def sanitize_error_details(cls, details: Dict[str, Any]) -> Dict[str, Any]:
        """Keep only safe fields from an error-details dict."""
        if not details:
            return {}
        safe_fields = {"operation", "folder", "uid", "mailbox"}
        return {k: v for k, v in details.items() if k in safe_fields}
